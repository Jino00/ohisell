// CoupangOps.tsx — 🔧 쿠팡 운영 패널
// 회사·기간별 매출 현황 + 상품별 상세. 컬럼 필터(▼) 드롭다운으로 값 선택 표시/숨김.
import { useState, useEffect, useCallback, useRef } from "react";
import { fetchSalesSummary, type SalesSummary, type SalesProductRow } from "../lib/api";

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
type SortKey = "product_name" | "revenue" | "ad_spend" | "conv_revenue" | "roas";
type SortDir = "asc" | "desc";
type ColKey = "revenue" | "ad_spend" | "conv_revenue" | "roas";

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
  return row.roas ? roasFmt(row.roas) : "—";
}
function numOf(s: string): number {
  const n = Number(s.replace(/[^0-9.-]/g, ""));
  return isNaN(n) ? 0 : n;
}

function SummaryCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="text-xl font-bold text-gray-900">{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
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

  async function syncNow() {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";
      const r = await fetch(`${API_BASE}/api/scheduler/trigger/auto_sync_orders`, { method: "POST" });
      const d = await r.json();
      setSyncMsg(d.detail ?? "동기화 완료");
      // 3초 후 데이터 재조회
      setTimeout(() => load(company, days), 3000);
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
      setColExcluded({});  // 데이터 바뀌면 필터 초기화
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

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
      const av = Number(sortKey === "roas" ? (a.roas ?? 0) : a[sortKey]);
      const bv = Number(sortKey === "roas" ? (b.roas ?? 0) : b[sortKey]);
      return mul * (av - bv);
    });

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
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold text-gray-900">🔧 쿠팡 운영 패널</h2>
          {data && <p className="text-xs text-gray-400 mt-0.5">{data.period.from} ~ {data.period.to}</p>}
        </div>
        <div className="flex items-center gap-2">
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
          <span className="text-xs text-gray-400 border-l border-gray-200 pl-2">
            ※ 쿠팡 API 약 1~2시간 지연 발생 가능
          </span>
        </div>
      </div>

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
            <div className="grid grid-cols-5 gap-3">
              <SummaryCard label="총 매출" value={loading ? "…" : won(s?.revenue)} />
              <SummaryCard label="수수료" value={loading ? "…" : won(s?.fee)} />
              <SummaryCard label="원가" value={loading ? "…" : won(s?.cost)} />
              <SummaryCard label="배송비" value={loading ? "…" : won(s?.shipping)} sub="Wing 1,900원/건" />
              <div className={`bg-white border-2 rounded-lg p-4 ${Number(s?.profit ?? 0) >= 0 ? "border-blue-200" : "border-red-200"}`}>
                <div className="text-xs text-gray-500 mb-1">이익 (광고비 제외)</div>
                <div className={`text-xl font-bold ${profitColor(s?.profit)}`}>{loading ? "…" : won(s?.profit)}</div>
                {s?.profit_rate && <div className="text-xs mt-0.5 text-gray-400">이익률 {pct(s.profit_rate)}</div>}
              </div>
            </div>
          </div>
          {/* 최신 광고 (다른 날짜 기준) */}
          <div>
            <div className="text-xs text-gray-400 font-medium mb-1.5 px-0.5">
              📣 광고 현황 ({data.ad_ref_date} 기준 — 최신 업로드)
            </div>
            <div className="grid grid-cols-3 gap-3">
              <SummaryCard label="광고비" value={loading ? "…" : won(s?.ad_spend)} />
              <SummaryCard label="광고 전환 매출" value={loading ? "…" : won(s?.conv_revenue)} />
              <SummaryCard label="RoAS" value={loading ? "…" : roasFmt(s?.roas)} sub={s?.roas ? "광고 전환매출 ÷ 광고비" : undefined} />
            </div>
          </div>
        </div>
      ) : (
        /* 어제·7일 등 — 동일 기간 */
        <div className="mb-6 space-y-2">
          <div className="grid grid-cols-6 gap-3">
            <SummaryCard label="총 매출" value={loading ? "…" : won(s?.revenue)} />
            <SummaryCard label="수수료" value={loading ? "…" : won(s?.fee)} />
            <SummaryCard label="원가" value={loading ? "…" : won(s?.cost)} />
            <SummaryCard label="광고비" value={loading ? "…" : won(s?.ad_spend)} />
            <SummaryCard label="배송비" value={loading ? "…" : won(s?.shipping)} sub="Wing 1,900원/건" />
            <div className={`bg-white border-2 rounded-lg p-4 ${Number(s?.profit ?? 0) >= 0 ? "border-blue-200" : "border-red-200"}`}>
              <div className="text-xs text-gray-500 mb-1">이익</div>
              <div className={`text-xl font-bold ${profitColor(s?.profit)}`}>{loading ? "…" : won(s?.profit)}</div>
              {s?.profit_rate && <div className="text-xs mt-0.5 text-gray-400">이익률 {pct(s.profit_rate)}</div>}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <SummaryCard label="광고 전환 매출" value={loading ? "…" : won(s?.conv_revenue)} />
            <SummaryCard label="RoAS" value={loading ? "…" : roasFmt(s?.roas)} sub={s?.roas ? "광고 전환매출 ÷ 광고비" : undefined} />
            <div />
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
          <input
            className="ml-auto border border-gray-300 rounded px-3 py-1.5 text-sm w-48"
            placeholder="상품명 검색…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

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
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">로딩 중…</td></tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
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
                </tr>
              ))
            )}
          </tbody>
          {filtered.length > 0 && (
            <tfoot className="bg-gray-50 border-t border-gray-200 text-sm font-semibold">
              <tr>
                <td className="px-4 py-2 text-gray-600" colSpan={2}>합계 ({filtered.length}개)</td>
                <td className="px-3 py-2 text-right">{won(String(filtered.reduce((a, r) => a + Number(r.revenue), 0)))}</td>
                <td className="px-3 py-2 text-right">{won(String(filtered.reduce((a, r) => a + Number(r.ad_spend), 0)))}</td>
                <td className="px-3 py-2 text-right">{won(String(filtered.reduce((a, r) => a + Number(r.conv_revenue), 0)))}</td>
                <td className="px-3 py-2 text-right">{(() => {
                  const sp = filtered.reduce((a, r) => a + Number(r.ad_spend), 0);
                  const cv = filtered.reduce((a, r) => a + Number(r.conv_revenue), 0);
                  return sp ? `${(cv / sp).toFixed(2)}x` : "—";
                })()}</td>
              </tr>
            </tfoot>
          )}
        </table>
        <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100">
          {filtered.length}개 표시 / 전체 {data?.by_product.length ?? 0}개
        </div>
      </div>
    </div>
  );
}
