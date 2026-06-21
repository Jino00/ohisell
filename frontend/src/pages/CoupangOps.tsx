// CoupangOps.tsx — 🔧 쿠팡 운영 패널
// 회사·기간별 매출 현황 + 상품별 상세. 컬럼 필터(▼) 드롭다운으로 값 선택 표시/숨김.
import { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { fetchSalesSummary, getCoupangAdCostDaily, requestAdCostRefresh, getAdCostRefreshStatus, type SalesSummary, type SalesProductRow } from "../lib/api";

// 오늘 날짜(KST) YYYY-MM-DD
function isoKSTDate(): string {
  const kst = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

const COMPANIES = [
  { value: "ALL", label: "전체" },
  { value: "오픽스", label: "오픽스" },
  { value: "오하이테크", label: "오하이테크" },
];
const PERIODS = [
  { label: "오늘", days: 0 },
  { label: "어제", days: 1 },
  { label: "7일", days: 7 },
  { label: "15일", days: 15 },
  { label: "30일", days: 30 },
];
const CHANNEL_TYPES = ["전체", "Wing", "로켓그로스", "로켓배송"] as const;
type ChannelType = (typeof CHANNEL_TYPES)[number];
type SortKey = "product_name" | "revenue" | "ad_spend" | "conv_revenue" | "roas" | "profit" | "profit_rate";
type SortDir = "asc" | "desc";
type ColKey = "revenue" | "ad_spend" | "conv_revenue" | "roas" | "profit" | "profit_rate";

function won(s: string | null | undefined) {
  if (s == null) return "—";
  const n = Math.round(Number(s));
  return `${n.toLocaleString("ko-KR")}원`;
}
function roasFmt(s: string | null | undefined) {
  if (s == null) return "—";
  return `${Number(s).toFixed(2)}x`;
}
function pct(s: string | null | undefined) {
  if (s == null) return "—";
  return `${Number(s).toFixed(1)}%`;
}
function profitColor(s: string | null | undefined) {
  if (s == null) return "text-gray-900";
  const n = Number(s);
  return n > 0 ? "text-blue-700" : n < 0 ? "text-red-600" : "text-gray-500";
}
function fmtVal(row: SalesProductRow, col: ColKey): string {
  if (col === "revenue") return won(row.revenue);
  if (col === "ad_spend") return won(row.ad_spend);
  if (col === "conv_revenue") return won(row.conv_revenue);
  if (col === "profit") return won(row.profit);
  if (col === "profit_rate") return row.profit_rate ? pct(row.profit_rate) : "—";
  return row.roas ? roasFmt(row.roas) : "—";
}
function numOf(s: string): number {
  const n = Number(s.replace(/[^0-9.-]/g, ""));
  return isNaN(n) ? 0 : n;
}
// 쿠팡 비용 카드 부가표기 — D-18 판매유형별 쿠팡 총비용
// 3P(Wing): 판매수수료+VAT / 2P(RG): 판매수수료+VAT+풀필먼트+RG광고
function feeSub(ratio: number | null | undefined): string | undefined {
  if (ratio == null || ratio >= 0.999) return "Wing 수수료+VAT · RG 수수료+풀필먼트+광고";
  return ratio <= 0.001 ? "정산 전 — 추정 포함" : `정산 ${Math.round(ratio * 100)}% · 나머지 추정`;
}
// 원가 카드 부가표기 — 원가 매핑 보유 매출 비율(미설정분은 0으로 빠져 이익 과대).
function costSub(cov: number | null | undefined): string | undefined {
  if (cov == null || cov >= 0.999) return undefined;
  return `${Math.round(cov * 100)}% 반영 (일부 원가 미설정)`;
}
// 배송·물류비 카드 부가표기 — Wing 한진 1,900/건 (RG 풀필먼트는 쿠팡 비용에 포함됨, D-18)
function shipSub(_rgFf: string | null | undefined): string {
  return "Wing 한진 1,900원/건 (RG 풀필먼트는 쿠팡 비용에 포함)";
}
// 오늘 광고비 카드 부가표기 — 마지막 fetch 시각(KST). 광고센터 누적은 실시간이라
// 마지막 갱신 이후 격차가 생긴다 → 갱신 버튼으로 최신화 안내(실시간 오인 방지).
function adTodaySub(synced: string | null | undefined): string {
  if (!synced) return "‘광고비 갱신’으로 최신화";
  const hhmm = synced.slice(11, 16);  // naive KST ISO → HH:MM
  return `${hhmm} 갱신 기준 · 버튼으로 최신화`;
}

// RoAS/이익률 색상 — 테이블·모바일 카드 공용(단일 출처).
function roasClass(s: string | null | undefined): string {
  if (s == null) return "text-gray-300";
  const n = Number(s);
  return n >= 3 ? "text-green-600 font-medium" : n >= 1 ? "text-gray-700" : "text-red-500";
}
function rateClass(s: string | null | undefined): string {
  if (s == null) return "text-gray-300";
  const n = Number(s);
  return n >= 20 ? "text-blue-600 font-medium" : n >= 0 ? "text-gray-700" : "text-red-500";
}

// 모바일 카드용 지표 셀 — 라벨 + 값(우측정렬 숫자). 테이블이 좁은 화면에서 잘리는 문제 대체.
function StatCell({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-gray-400 mb-0.5 break-keep">{label}</div>
      <div className={`text-xs tabular-nums break-keep ${valueClass ?? "text-gray-800"}`}>{value}</div>
    </div>
  );
}

function SummaryCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3 sm:p-4">
      <div className="text-xs text-gray-500 mb-1 break-keep">{label}</div>
      <div className="text-base sm:text-xl font-bold text-gray-900 tabular-nums break-keep">{value}</div>
      {sub && <div className="text-[11px] sm:text-xs text-gray-400 mt-0.5 break-keep leading-tight">{sub}</div>}
    </div>
  );
}

export default function CoupangOps() {
  const [company, setCompany] = useState("ALL");
  const [days, setDays] = useState(7);
  const [channelFilter, setChannelFilter] = useState<ChannelType>("전체");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("revenue");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // 컬럼 필터: 제외할 값 집합 (비어있으면 전체 표시)
  const [colExcluded, setColExcluded] = useState<Record<string, Set<string>>>({});
  const [openCol, setOpenCol] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const [data, setData] = useState<SalesSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  // 광고 쿠키 설정 (advertising.coupang.com)
  // 전역 만료 배너 CTA(?adcookie=open)로 진입하면 패널 자동 펼침
  const [searchParams] = useSearchParams();
  const [showAdSettings, setShowAdSettings] = useState(searchParams.get("adcookie") === "open");
  const [adCurl, setAdCurl] = useState("");
  const [adCookieStatus, setAdCookieStatus] = useState<{ configured: boolean; status: string; last_success_at: string | null; last_error: string | null } | null>(null);
  const [adSaving, setAdSaving] = useState(false);
  const [adSyncMsg, setAdSyncMsg] = useState<string | null>(null);
  const [todayAdCost, setTodayAdCost] = useState<number | null>(null);  // 오늘 라이브 광고비(coupang_ad_cost_daily)
  const [adRefreshing, setAdRefreshing] = useState(false);

  const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

  async function triggerSync() {
    const r = await fetch(`${API_BASE}/api/scheduler/trigger/auto_sync_orders`, { method: "POST" });
    if (!r.ok) throw new Error(`sync failed: ${r.status}`);
    return r.json();
  }

  async function loadAdCookieStatus() {
    try {
      const r = await fetch(`${API_BASE}/api/coupang/ops/ad-cost/cookie/status`);
      if (r.ok) setAdCookieStatus(await r.json());
    } catch { /* 조용히 실패 */ }
  }

  async function saveAdCookie() {
    if (!adCurl.trim()) return;
    setAdSaving(true);
    setAdSyncMsg(null);
    try {
      const r = await fetch(`${API_BASE}/api/coupang/ops/ad-cost/cookie`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ curl: adCurl }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "저장 실패");
      setAdSyncMsg("✅ 쿠키 저장 완료");
      setAdCurl("");
      await loadAdCookieStatus();
    } catch (e: any) {
      setAdSyncMsg("❌ " + e.message);
    } finally {
      setAdSaving(false);
    }
  }

  // 오늘 라이브 광고비(coupang_ad_cost_daily, Mac 페처가 채움) 로드.
  async function loadTodayAdCost() {
    try {
      const today = isoKSTDate();
      const { costs } = await getCoupangAdCostDaily(today, today);
      const total = costs.reduce((s, c) => s + (c.day_cost || 0), 0);
      setTodayAdCost(costs.length ? total : null);
    } catch { /* 조용히 실패 */ }
  }

  // "광고비 갱신" — Jino Mac 페처를 깨워 오늘 쿠팡 광고비를 즉시 가져온다(Akamai로 prod 직접 fetch 불가).
  // request-refresh → Mac 데몬이 fetch·push → last_success_at 변화를 폴링해 완료 감지 → 오늘 광고비 리로드.
  async function refreshAdCostNow() {
    setAdRefreshing(true);
    setAdSyncMsg("Mac에서 광고비 가져오는 중… (~20초, 첫 갱신이면 Mac 로그인 창 확인)");
    try {
      const baseline = (await getAdCostRefreshStatus()).last_success_at;
      await requestAdCostRefresh();
      const deadline = Date.now() + 215000; // 215초 — 데몬 로그인 대기(180s)+fetch 여유까지 커버
      let done = false;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await getAdCostRefreshStatus();
        if (st.last_success_at && st.last_success_at !== baseline) { done = true; break; }
      }
      if (done) {
        await loadTodayAdCost();
        await loadAdCookieStatus();
        setAdSyncMsg("✅ 광고비 갱신 완료");
        setTimeout(() => setAdSyncMsg(null), 4000);
      } else {
        setAdSyncMsg("⚠️ Mac 응답 없음 — Mac이 켜져 있는지, 첫 갱신이면 로그인 창을 확인하세요.");
      }
    } catch (e: any) {
      setAdSyncMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      setAdRefreshing(false);
    }
  }

  async function syncNow() {
    setSyncing(true);
    setSyncMsg(null);
    try {
      await triggerSync();
      setSyncMsg("동기화 완료");
      await load(company, days);
      await loadAdCookieStatus();
      await loadTodayAdCost();
    } catch (e: any) {
      setSyncMsg("동기화 실패: " + e.message);
    } finally {
      setSyncing(false);
    }
  }

  const load = useCallback(async (c: string, d: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchSalesSummary(c, d));
      setColExcluded({});
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // 페이지 접속 시 자동 sync → 완료 후 데이터 로드
  useEffect(() => {
    let cancelled = false;
    setSyncing(true);
    (async () => {
      try { await triggerSync(); } catch { /* sync 실패해도 기존 데이터 표시 */ }
      if (!cancelled) { setSyncing(false); load(company, days); loadAdCookieStatus(); loadTodayAdCost(); }
    })();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);  // 마운트 1회만

  useEffect(() => { load(company, days); }, [company, days, load]);

  // 드롭다운 외부 클릭 시 닫기
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpenCol(null);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  const allRows = data?.by_product ?? [];

  // 컬럼별 unique 값 목록 (전체 데이터 기준)
  function uniqueVals(col: ColKey): string[] {
    const vals = [...new Set(allRows.map((r) => fmtVal(r, col)))];
    return vals.sort((a, b) => numOf(a) - numOf(b));
  }

  // 필터 적용
  const filtered = allRows
    .filter((row) => {
      const matchCh = channelFilter === "전체" || row.channel_type === channelFilter;
      const q = search.toLowerCase();
      const matchQ =
        !q ||
        row.product_name.toLowerCase().includes(q) ||
        row.option_name.toLowerCase().includes(q);
      return matchCh && matchQ;
    })
    .filter((row) => {
      for (const [col, excluded] of Object.entries(colExcluded)) {
        if (excluded.size > 0 && excluded.has(fmtVal(row, col as ColKey))) return false;
      }
      return true;
    })
    .sort((a, b) => {
      const mul = sortDir === "desc" ? -1 : 1;
      if (sortKey === "product_name")
        return mul * `${a.product_name},${a.option_name}`.localeCompare(`${b.product_name},${b.option_name}`, "ko");
      const getV = (r: SalesProductRow) => {
        if (sortKey === "roas") return Number(r.roas ?? 0);
        if (sortKey === "profit_rate") return Number(r.profit_rate ?? 0);
        return Number((r as any)[sortKey] ?? 0);
      };
      const av = getV(a);
      const bv = getV(b);
      return mul * (av - bv);
    });

  // 합계(테이블 tfoot + 모바일 카드 공용) — 단일 출처로 중복 제거.
  const totals = {
    revenue: filtered.reduce((a, r) => a + Number(r.revenue), 0),
    ad_spend: filtered.reduce((a, r) => a + Number(r.ad_spend), 0),
    conv_revenue: filtered.reduce((a, r) => a + Number(r.conv_revenue), 0),
    profit: filtered.reduce((a, r) => a + Number(r.profit), 0),
  };
  const totalRoas = totals.ad_spend ? `${(totals.conv_revenue / totals.ad_spend).toFixed(2)}x` : "—";
  const totalRate = totals.revenue ? `${((totals.profit / totals.revenue) * 100).toFixed(1)}%` : "—";

  // 컬럼 헤더 (정렬 + 필터 드롭다운)
  function ColHeader({ col, label, align = "right" }: { col: ColKey; label: string; align?: "left" | "right" }) {
    const vals = uniqueVals(col);
    const excluded = colExcluded[col] ?? new Set<string>();
    const hasFilter = excluded.size > 0;
    const isOpen = openCol === col;
    const isSorted = sortKey === col;

    function toggleVal(v: string) {
      setColExcluded((prev) => {
        const next = new Set(prev[col] ?? []);
        next.has(v) ? next.delete(v) : next.add(v);
        return { ...prev, [col]: next };
      });
    }
    function selectAll() { setColExcluded((prev) => ({ ...prev, [col]: new Set() })); }
    function deselectAll() { setColExcluded((prev) => ({ ...prev, [col]: new Set(vals) })); }

    return (
      <th className={`px-3 py-2 text-${align} select-none`}>
        <div className={`inline-flex items-center gap-0 ${align === "right" ? "justify-end" : ""} w-full`}>

          {/* 정렬 버튼 — 라벨 전체 영역 클릭 */}
          <button
            className={`flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-gray-200 cursor-pointer transition-colors ${isSorted ? "text-blue-600 font-semibold" : "text-gray-500 hover:text-gray-800"}`}
            onClick={() => toggleSort(col as SortKey)}
            title={isSorted ? (sortDir === "desc" ? "내림차순 정렬 중 — 클릭하면 오름차순" : "오름차순 정렬 중 — 클릭하면 내림차순") : "클릭하여 정렬"}
          >
            {label}
            <span className={`text-xs ml-0.5 ${isSorted ? "text-blue-500" : "text-gray-300"}`}>
              {isSorted ? (sortDir === "desc" ? "↓" : "↑") : "↕"}
            </span>
          </button>

          {/* 필터 버튼 — 깔때기 아이콘으로 정렬 버튼과 명확히 구분 */}
          <div className="relative" ref={isOpen ? dropdownRef : undefined}>
            <button
              onClick={(e) => { e.stopPropagation(); setOpenCol(isOpen ? null : col); }}
              className={`ml-1 text-xs px-1 py-0.5 rounded border transition-colors ${
                hasFilter
                  ? "bg-blue-500 text-white border-blue-500"
                  : "text-gray-400 border-gray-300 hover:text-gray-700 hover:bg-gray-100"
              }`}
              title="값 필터"
            >
              {hasFilter ? "🔵" : "⊟"}
            </button>
            {isOpen && (
              <div
                className="absolute top-full right-0 z-50 bg-white border border-gray-200 rounded-lg shadow-xl mt-1 w-44"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex gap-2 px-3 py-2 border-b border-gray-100 text-xs font-medium text-gray-600">
                  값 필터
                  <span className="ml-auto flex gap-2">
                    <button onClick={selectAll} className="text-blue-600 hover:underline">전체</button>
                    <button onClick={deselectAll} className="text-red-500 hover:underline">해제</button>
                  </span>
                </div>
                <div className="max-h-52 overflow-y-auto py-1">
                  {vals.map((v) => (
                    <label
                      key={v}
                      className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-gray-50 text-xs text-gray-700"
                    >
                      <input type="checkbox" className="accent-blue-500" checked={!excluded.has(v)} onChange={() => toggleVal(v)} />
                      {v}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </th>
    );
  }

  const s = data?.summary;
  const activeFilters = Object.values(colExcluded).filter((s) => s.size > 0).length;

  return (
    <div onClick={() => setOpenCol(null)}>
      {/* ── 헤더 ── */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
        <div>
          <h2 className="text-xl font-bold text-gray-900">🔧 쿠팡 운영 패널</h2>
          {data && <p className="text-xs text-gray-400 mt-0.5">{data.period.from} ~ {data.period.to}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1">
            {PERIODS.map((p) => (
              <button
                key={p.days}
                onClick={() => setDays(p.days)}
                className={`px-3 py-1.5 rounded text-sm font-medium ${
                  days === p.days ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <button
            onClick={syncNow}
            disabled={syncing}
            className="px-3 py-1.5 rounded text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 flex items-center gap-1"
            title="최신 주문 동기화 후 새로고침"
          >
            {syncing ? "동기화 중…" : "🔄 동기화"}
          </button>
          {syncMsg && <span className="text-xs text-gray-500">{syncMsg} (3초 후 갱신)</span>}
          {/* 오늘 라이브 광고비 + 즉시 갱신(버튼 클릭 시 Mac 페처가 가져옴) */}
          <span className="text-sm text-gray-600 border-l border-gray-200 pl-2">
            오늘 광고비{" "}
            <span className="font-semibold text-gray-900">
              {todayAdCost == null ? "—" : `${todayAdCost.toLocaleString("ko-KR")}원`}
            </span>
          </span>
          <button
            onClick={refreshAdCostNow}
            disabled={adRefreshing}
            className="px-3 py-1.5 rounded text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1"
            title="Jino Mac에서 오늘 광고비를 지금 가져옵니다(~20초). Mac이 켜져 있어야 합니다."
          >
            {adRefreshing ? "갱신 중…" : "📣 광고비 갱신"}
          </button>
          {adSyncMsg && <span className="text-xs text-gray-500">{adSyncMsg}</span>}
          <button
            onClick={() => setShowAdSettings((v) => !v)}
            className={`px-3 py-1.5 rounded text-sm font-medium border transition-colors ${
              adCookieStatus?.status === "green" ? "border-green-400 text-green-700 bg-green-50 hover:bg-green-100"
              : adCookieStatus?.status === "red" ? "border-red-400 text-red-700 bg-red-50 hover:bg-red-100"
              : "border-gray-300 text-gray-600 bg-gray-50 hover:bg-gray-100"
            }`}
            title="광고비 쿠키 설정(레거시)"
          >
            ⚙️ {adCookieStatus?.status === "green" ? "🟢" : adCookieStatus?.status === "red" ? "🔴" : "⬜"}
          </button>
          <span className="hidden sm:inline text-xs text-gray-400 border-l border-gray-200 pl-2">
            ※ 쿠팡 API 약 1~2시간 지연 발생 가능
          </span>
        </div>
      </div>

      {/* ── 광고 쿠키 설정 패널 ── */}
      {showAdSettings && (
        <div className="mb-4 bg-orange-50 border border-orange-200 rounded-lg p-4 text-sm">
          <div className="flex items-center gap-2 mb-3">
            <span className="font-semibold text-orange-800">📣 쿠팡 광고비 쿠키 설정</span>
            <span className="text-xs text-gray-500">advertising.coupang.com 세션쿠키 — 매일 자정 자동 동기화</span>
            {adCookieStatus && (
              <span className={`ml-auto text-xs px-2 py-0.5 rounded-full font-medium ${
                adCookieStatus.status === "green" ? "bg-green-100 text-green-700"
                : adCookieStatus.status === "red" ? "bg-red-100 text-red-700"
                : "bg-gray-100 text-gray-500"
              }`}>
                {adCookieStatus.status === "green" ? "🟢 정상"
                  : adCookieStatus.status === "red" ? "🔴 만료"
                  : adCookieStatus.configured ? "⬜ 미확인" : "⬜ 미설정"}
                {adCookieStatus.last_success_at && (
                  <span className="ml-1 text-gray-400">({adCookieStatus.last_success_at.slice(0, 10)})</span>
                )}
              </span>
            )}
          </div>
          <div className="text-xs text-gray-500 mb-2">
            1. advertising.coupang.com 광고 대시보드 접속 → DevTools Network → cost 요청 우클릭 → "Copy as cURL" → 아래 붙여넣기
          </div>
          <div className="flex gap-2">
            <textarea
              value={adCurl}
              onChange={(e) => setAdCurl(e.target.value)}
              placeholder="curl 'https://advertising.coupang.com/...' ..."
              className="flex-1 h-16 px-3 py-2 text-xs border border-gray-300 rounded-lg font-mono resize-none focus:outline-none focus:ring-1 focus:ring-orange-400"
            />
            <div className="flex flex-col gap-1">
              <button
                onClick={saveAdCookie}
                disabled={adSaving || !adCurl.trim()}
                className="px-4 py-2 rounded text-xs font-medium bg-orange-600 text-white hover:bg-orange-700 disabled:opacity-50"
              >
                {adSaving ? "저장 중…" : "💾 저장"}
              </button>
              <button
                onClick={refreshAdCostNow}
                disabled={adRefreshing}
                title="Jino Mac에서 오늘 광고비를 지금 가져옵니다(~20초). Mac이 켜져 있어야 합니다."
                className="px-4 py-2 rounded text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {adRefreshing ? "갱신 중…" : "📣 광고비 갱신"}
              </button>
            </div>
          </div>
          {adSyncMsg && <div className="mt-2 text-xs text-gray-700">{adSyncMsg}</div>}
          {adCookieStatus?.last_error && (
            <div className="mt-1 text-xs text-red-600">오류: {adCookieStatus.last_error}</div>
          )}
        </div>
      )}

      {/* ── 회사 탭 ── */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {COMPANIES.map((c) => (
          <button
            key={c.value}
            onClick={() => setCompany(c.value)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              company === c.value ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {error && <div className="bg-red-50 text-red-700 rounded px-4 py-2 text-sm mb-4">{error}</div>}

      {/* ── 요약 카드 ── */}
      {data?.ad_ref_date ? (
        /* 오늘 선택 + 광고 기준일이 다를 때 — 판매/광고 섹션 분리 */
        <div className="mb-6 space-y-3">
          {/* 오늘 판매 */}
          <div>
            <div className="text-xs text-gray-400 font-medium mb-1.5 px-0.5">
              📦 오늘 판매 ({data.period.from})
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 sm:gap-3">
              <SummaryCard label="총 매출" value={loading ? "…" : won(s?.revenue)} />
              <SummaryCard label="쿠팡 비용" value={loading ? "…" : won(s?.fee)} sub={feeSub(s?.fee_actual_ratio)} />
              <SummaryCard label="원가" value={loading ? "…" : won(s?.cost)} sub={costSub(s?.cost_coverage)} />
              <SummaryCard label="배송·물류비" value={loading ? "…" : won(s?.shipping)} sub={shipSub(s?.rg_fulfillment)} />
              <div className={`bg-white border-2 rounded-lg p-3 sm:p-4 ${Number(s?.profit_excl_ad ?? 0) >= 0 ? "border-blue-200" : "border-red-200"}`}>
                <div className="text-xs text-gray-500 mb-1 break-keep">이익 (광고비 제외)</div>
                <div className={`text-base sm:text-xl font-bold tabular-nums break-keep ${profitColor(s?.profit_excl_ad)}`}>{loading ? "…" : won(s?.profit_excl_ad)}</div>
                {s?.profit_rate_excl_ad && <div className="text-[11px] sm:text-xs mt-0.5 text-gray-400">이익률 {pct(s.profit_rate_excl_ad)}</div>}
              </div>
            </div>
          </div>
          {/* 광고 현황 — 광고비는 오늘 실시간(일자단위), 전환·RoAS·옵션내역은 익일 확정 */}
          <div>
            <div className="text-xs text-gray-400 font-medium mb-1.5 px-0.5">
              📣 광고 현황 (광고비=오늘 실시간 · 전환매출/RoAS는 익일 확정)
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-3">
              <SummaryCard
                label="광고비 (오늘)"
                value={loading ? "…" : (s?.ad_today != null ? won(s.ad_today) : "익일 확정")}
                sub={s?.ad_today != null ? adTodaySub(s?.ad_today_synced_at) : `어제(${data.ad_ref_date}) ${won(s?.ad_spend)}`}
              />
              <SummaryCard
                label="광고 전환 매출"
                value={loading ? "…" : "익일 확정"}
                sub={`어제(${data.ad_ref_date}) ${won(s?.conv_revenue)}`}
              />
              <SummaryCard
                label="RoAS"
                value={loading ? "…" : "익일 확정"}
                sub={s?.roas ? `어제(${data.ad_ref_date}) ${roasFmt(s.roas)}` : undefined}
              />
            </div>
          </div>
        </div>
      ) : (
        /* 어제·7일 등 — 동일 기간 */
        <div className="mb-6 space-y-2">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 sm:gap-3">
            <SummaryCard label="총 매출" value={loading ? "…" : won(s?.revenue)} />
            <SummaryCard label="수수료" value={loading ? "…" : won(s?.fee)} sub={feeSub(s?.fee_actual_ratio)} />
            <SummaryCard label="원가" value={loading ? "…" : won(s?.cost)} sub={costSub(s?.cost_coverage)} />
            <SummaryCard label="광고비" value={loading ? "…" : won(s?.ad_spend)} />
            <SummaryCard label="배송·물류비" value={loading ? "…" : won(s?.shipping)} sub={shipSub(s?.rg_fulfillment)} />
            <div className={`bg-white border-2 rounded-lg p-3 sm:p-4 ${Number(s?.profit ?? 0) >= 0 ? "border-blue-200" : "border-red-200"}`}>
              <div className="text-xs text-gray-500 mb-1 break-keep">이익</div>
              <div className={`text-base sm:text-xl font-bold tabular-nums break-keep ${profitColor(s?.profit)}`}>{loading ? "…" : won(s?.profit)}</div>
              {s?.profit_rate && <div className="text-[11px] sm:text-xs mt-0.5 text-gray-400">이익률 {pct(s.profit_rate)}</div>}
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-3">
            <SummaryCard label="광고 전환 매출" value={loading ? "…" : won(s?.conv_revenue)} />
            <SummaryCard label="RoAS" value={loading ? "…" : roasFmt(s?.roas)} sub={s?.roas ? "광고 전환매출 ÷ 광고비" : undefined} />
            <div className="hidden sm:block" />
          </div>
        </div>
      )}

      {/* ── 상품별 테이블 ── */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        {/* 필터 바 */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50 flex-wrap">
          <span className="text-sm font-medium text-gray-700">상품별 현황</span>
          <div className="flex gap-1">
            {CHANNEL_TYPES.map((ct) => (
              <button
                key={ct}
                onClick={() => setChannelFilter(ct)}
                className={`px-2.5 py-1 rounded text-xs font-medium ${
                  channelFilter === ct ? "bg-gray-800 text-white" : "bg-white border border-gray-300 text-gray-600 hover:bg-gray-100"
                }`}
              >
                {ct}
              </button>
            ))}
          </div>
          {activeFilters > 0 && (
            <button
              onClick={() => setColExcluded({})}
              className="text-xs text-red-500 hover:underline border-l border-gray-200 pl-3"
            >
              필터 초기화 ({activeFilters}개 적용 중)
            </button>
          )}
          {/* 모바일 정렬 — 카드 뷰엔 컬럼 헤더가 없어 별도 정렬 컨트롤 제공(데스크톱은 헤더 클릭).
              방향(↓/↑)은 토글 버튼으로 가시화 — select만으론 데스크톱에서 바뀐 방향이 숨겨짐(codex). */}
          <div className="md:hidden flex items-center gap-1">
            <select
              className="border border-gray-300 rounded px-2 py-1.5 text-xs bg-white"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              aria-label="정렬 기준"
            >
              <option value="revenue">매출순</option>
              <option value="profit">이익순</option>
              <option value="profit_rate">이익률순</option>
              <option value="ad_spend">광고비순</option>
              <option value="conv_revenue">전환매출순</option>
              <option value="roas">RoAS순</option>
            </select>
            <button
              onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
              className="px-2 py-1.5 rounded border border-gray-300 text-xs text-gray-600 bg-white hover:bg-gray-100"
              aria-label={sortDir === "desc" ? "내림차순(클릭하여 오름차순)" : "오름차순(클릭하여 내림차순)"}
              title={sortDir === "desc" ? "내림차순" : "오름차순"}
            >
              {sortDir === "desc" ? "↓" : "↑"}
            </button>
          </div>
          <input
            className="w-full sm:w-48 sm:ml-auto border border-gray-300 rounded px-3 py-1.5 text-sm"
            placeholder="상품명 검색…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {/* 데스크톱·태블릿: 테이블 (모바일은 아래 카드 리스트로 대체) */}
        <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-xs border-b border-gray-100">
            <tr>
              {/* 상품명 — 정렬만, 드롭다운 필터 없음 (값이 너무 다양) */}
              <th
                className="px-4 py-2 text-left cursor-pointer hover:text-gray-800 select-none"
                onClick={() => toggleSort("product_name")}
              >
                상품명
                {sortKey === "product_name" ? (
                  <span className="ml-0.5 text-blue-500">{sortDir === "desc" ? "↓" : "↑"}</span>
                ) : (
                  <span className="ml-0.5 text-gray-300">↕</span>
                )}
              </th>
              <th className="px-3 py-2 text-center">채널</th>
              <ColHeader col="revenue" label="총 매출" />
              <ColHeader col="ad_spend" label="광고비" />
              <ColHeader col="conv_revenue" label="광고 전환매출" />
              <ColHeader col="roas" label="RoAS" />
              <ColHeader col="profit" label="이익" />
              <ColHeader col="profit_rate" label="이익률" />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">로딩 중…</td></tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-400">
                  {data?.by_product.length === 0 ? "데이터 없음 — 동기화 후 조회하세요" : "검색/필터 결과 없음"}
                </td>
              </tr>
            ) : (
              filtered.map((row, i) => (
                <tr key={i} className="border-t border-gray-50 hover:bg-gray-50">
                  <td className="px-4 py-2 max-w-[380px]">
                    <div
                      className="text-gray-900 text-sm truncate"
                      title={row.option_name ? `${row.product_name}, ${row.option_name}` : row.product_name}
                    >
                      {row.product_name}
                      {row.option_name && <span className="text-gray-400">, {row.option_name}</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      row.channel_type === "Wing" ? "bg-blue-50 text-blue-700"
                      : row.channel_type === "로켓그로스" ? "bg-orange-50 text-orange-700"
                      : "bg-purple-50 text-purple-700"
                    }`}>
                      {row.channel_type}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-medium">{won(row.revenue)}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{won(row.ad_spend)}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{won(row.conv_revenue)}</td>
                  <td className="px-3 py-2 text-right">
                    {row.roas ? (
                      <span className={
                        Number(row.roas) >= 3 ? "text-green-600 font-medium"
                        : Number(row.roas) >= 1 ? "text-gray-700"
                        : "text-red-500"
                      }>
                        {roasFmt(row.roas)}
                      </span>
                    ) : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-3 py-2 text-right font-medium">
                    <span className={profitColor(row.profit)}>{won(row.profit)}</span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {row.profit_rate ? (
                      <span className={
                        Number(row.profit_rate) >= 20 ? "text-blue-600 font-medium"
                        : Number(row.profit_rate) >= 0 ? "text-gray-700"
                        : "text-red-500"
                      }>
                        {pct(row.profit_rate)}
                      </span>
                    ) : <span className="text-gray-300">—</span>}
                  </td>
                </tr>
              ))
            )}
          </tbody>
          {filtered.length > 0 && (
            <tfoot className="bg-gray-50 border-t border-gray-200 text-sm font-semibold">
              <tr>
                <td className="px-4 py-2 text-gray-600" colSpan={2}>합계 ({filtered.length}개)</td>
                <td className="px-3 py-2 text-right">{won(String(totals.revenue))}</td>
                <td className="px-3 py-2 text-right">{won(String(totals.ad_spend))}</td>
                <td className="px-3 py-2 text-right">{won(String(totals.conv_revenue))}</td>
                <td className="px-3 py-2 text-right">{totalRoas}</td>
                <td className="px-3 py-2 text-right">
                  <span className={totals.profit >= 0 ? "text-blue-700" : "text-red-500"}>{won(String(totals.profit))}</span>
                </td>
                <td className="px-3 py-2 text-right">{totalRate}</td>
              </tr>
            </tfoot>
          )}
        </table>
        </div>

        {/* 모바일: 상품 카드 리스트 (테이블이 좁은 화면에서 잘리는 문제 대체) */}
        <div className="md:hidden">
          {loading ? (
            <div className="px-4 py-8 text-center text-gray-400 text-sm">로딩 중…</div>
          ) : filtered.length === 0 ? (
            <div className="px-4 py-8 text-center text-gray-400 text-sm">
              {data?.by_product.length === 0 ? "데이터 없음 — 동기화 후 조회하세요" : "검색/필터 결과 없음"}
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {filtered.map((row) => (
                <li key={`${row.product_name}|${row.option_name}|${row.channel_type}`} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="text-sm text-gray-900 leading-snug break-keep">
                      {row.product_name}
                      {row.option_name && <span className="text-gray-400">, {row.option_name}</span>}
                    </div>
                    <span className={`shrink-0 inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      row.channel_type === "Wing" ? "bg-blue-50 text-blue-700"
                      : row.channel_type === "로켓그로스" ? "bg-orange-50 text-orange-700"
                      : "bg-purple-50 text-purple-700"
                    }`}>{row.channel_type}</span>
                  </div>
                  <div className="grid grid-cols-3 gap-x-3 gap-y-2">
                    <StatCell label="총 매출" value={won(row.revenue)} valueClass="text-gray-900 font-medium" />
                    <StatCell label="광고비" value={won(row.ad_spend)} />
                    <StatCell label="광고 전환매출" value={won(row.conv_revenue)} />
                    <StatCell label="RoAS" value={row.roas ? roasFmt(row.roas) : "—"} valueClass={roasClass(row.roas)} />
                    <StatCell label="이익" value={won(row.profit)} valueClass={`font-medium ${profitColor(row.profit)}`} />
                    <StatCell label="이익률" value={row.profit_rate ? pct(row.profit_rate) : "—"} valueClass={rateClass(row.profit_rate)} />
                  </div>
                </li>
              ))}
            </ul>
          )}
          {filtered.length > 0 && (
            <div className="px-4 py-3 bg-gray-50 border-t border-gray-200">
              <div className="text-xs font-semibold text-gray-600 mb-2">합계 ({filtered.length}개)</div>
              <div className="grid grid-cols-3 gap-x-3 gap-y-2">
                <StatCell label="총 매출" value={won(String(totals.revenue))} valueClass="text-gray-900 font-semibold" />
                <StatCell label="광고비" value={won(String(totals.ad_spend))} valueClass="font-semibold" />
                <StatCell label="광고 전환매출" value={won(String(totals.conv_revenue))} valueClass="font-semibold" />
                <StatCell label="RoAS" value={totalRoas} valueClass="font-semibold text-gray-800" />
                <StatCell label="이익" value={won(String(totals.profit))} valueClass={`font-semibold ${totals.profit >= 0 ? "text-blue-700" : "text-red-500"}`} />
                <StatCell label="이익률" value={totalRate} valueClass="font-semibold text-gray-800" />
              </div>
            </div>
          )}
        </div>

        <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100">
          {filtered.length}개 표시 / 전체 {data?.by_product.length ?? 0}개
        </div>
      </div>
    </div>
  );
}
