// NaverOps.tsx — 🛒 네이버 스마트스토어 운영 패널
// 기간별 매출 현황 + 상품별 상세 (쿠팡 패널 단순화 버전)
import { useState, useEffect, useCallback, useRef } from "react";
import {
  fetchNaverSalesSummary, fetchGfaStatus, uploadGfaCsv,
  type NaverSalesSummary, type NaverSalesProductRow, type GfaStatus,
} from "../lib/api";

// 마지막 업로드일로부터 경과 일수 (로컬 기준). null이면 데이터 없음.
function daysSince(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null;
  const d = new Date(dateStr + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((today.getTime() - d.getTime()) / 86400000);
}

const PERIODS = [
  { label: "오늘", days: 0 },
  { label: "어제", days: 1 },
  { label: "7일", days: 7 },
  { label: "15일", days: 15 },
  { label: "30일", days: 30 },
];

type SortKey = "product_name" | "revenue" | "profit" | "profit_rate";
type SortDir = "asc" | "desc";
type ColKey = "revenue" | "profit" | "profit_rate";

function won(s: string | null | undefined) {
  if (s == null) return "—";
  const n = Math.round(Number(s));
  return `${n.toLocaleString("ko-KR")}원`;
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
function fmtVal(row: NaverSalesProductRow, col: ColKey): string {
  if (col === "revenue") return won(row.revenue);
  if (col === "profit") return won(row.profit);
  return row.profit_rate ? pct(row.profit_rate) : "—";
}

function SummaryCard({ label, value, sub, highlight }: {
  label: string; value: string; sub?: string; highlight?: "blue" | "red";
}) {
  const border = highlight === "blue"
    ? "border-blue-400 bg-blue-50"
    : highlight === "red"
    ? "border-red-400 bg-red-50"
    : "border-gray-200 bg-white";
  return (
    <div className={`border rounded-lg p-4 ${border}`}>
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-xl font-bold ${highlight === "blue" ? "text-blue-700" : highlight === "red" ? "text-red-600" : "text-gray-900"}`}>{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

// 컬럼 값 필터 드롭다운
function ColFilter({
  col, rows, active, onClose, onSelect,
}: {
  col: ColKey;
  rows: NaverSalesProductRow[];
  active: Set<string>;
  onClose: () => void;
  onSelect: (vals: Set<string>) => void;
}) {
  const uniq = Array.from(new Set(rows.map((r) => fmtVal(r, col)))).sort((a, b) => {
    const na = Number(a.replace(/[^0-9.-]/g, ""));
    const nb = Number(b.replace(/[^0-9.-]/g, ""));
    return isNaN(na) || isNaN(nb) ? a.localeCompare(b) : nb - na;
  });
  const [sel, setSel] = useState<Set<string>>(active.size ? active : new Set(uniq));
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div ref={ref} className="absolute z-50 bg-white border border-gray-200 rounded shadow-lg p-2 min-w-[140px] max-h-64 overflow-y-auto">
      <div className="flex gap-1 mb-1">
        <button className="text-xs text-blue-600 underline" onClick={() => setSel(new Set(uniq))}>전체</button>
        <span className="text-gray-300">|</span>
        <button className="text-xs text-blue-600 underline" onClick={() => setSel(new Set())}>없음</button>
      </div>
      {uniq.map((v) => (
        <label key={v} className="flex items-center gap-1 text-xs py-0.5 cursor-pointer hover:bg-gray-50">
          <input type="checkbox" checked={sel.has(v)} onChange={(e) => {
            const next = new Set(sel);
            if (e.target.checked) next.add(v); else next.delete(v);
            setSel(next);
          }} />
          <span>{v}</span>
        </label>
      ))}
      <button
        className="mt-1 w-full text-xs bg-blue-600 text-white rounded py-1"
        onClick={() => { onSelect(sel); onClose(); }}
      >적용</button>
    </div>
  );
}

export default function NaverOps() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<NaverSalesSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("revenue");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [colFilters, setColFilters] = useState<Partial<Record<ColKey, Set<string>>>>({});
  const [openFilter, setOpenFilter] = useState<ColKey | null>(null);
  const [gfa, setGfa] = useState<GfaStatus | null>(null);
  const [gfaUploading, setGfaUploading] = useState(false);
  const [gfaMsg, setGfaMsg] = useState<string | null>(null);
  const gfaFileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setData(await fetchNaverSalesSummary(days));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

  const loadGfa = useCallback(async () => {
    try { setGfa(await fetchGfaStatus()); } catch { /* silent */ }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadGfa(); }, [loadGfa]);

  async function handleGfaUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.endsWith(".csv")) { setGfaMsg("CSV 파일만 업로드 가능합니다."); return; }
    setGfaUploading(true); setGfaMsg(null);
    try {
      const r = await uploadGfaCsv(file);
      setGfaMsg(`✅ ${r.inserted}일치 등록 (${r.date_from}~${r.date_to}, 총 ${Number(r.total_spend).toLocaleString("ko-KR")}원)`);
      await Promise.all([loadGfa(), load()]);
    } catch (err) {
      setGfaMsg(err instanceof Error ? err.message : "업로드 실패");
    } finally {
      setGfaUploading(false);
      if (gfaFileRef.current) gfaFileRef.current.value = "";
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      await fetch("/api/scheduler/trigger/auto_sync_orders", { method: "POST" });
      setTimeout(() => { setSyncing(false); load(); }, 3000);
    } catch {
      setSyncing(false);
    }
  }

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir("desc"); }
  }

  const rows: NaverSalesProductRow[] = data?.by_product ?? [];

  // 컬럼 필터 적용
  const filtered = rows.filter((r) => {
    for (const [col, vals] of Object.entries(colFilters) as [ColKey, Set<string>][]) {
      if (vals && vals.size > 0 && !vals.has(fmtVal(r, col))) return false;
    }
    return true;
  });

  // 정렬
  const sorted = [...filtered].sort((a, b) => {
    let av: number | string, bv: number | string;
    if (sortKey === "product_name") {
      av = a.product_name; bv = b.product_name;
      return sortDir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    }
    av = Number(a[sortKey] ?? 0); bv = Number(b[sortKey] ?? 0);
    return sortDir === "asc" ? av - bv : bv - av;
  });

  const s = data?.summary;
  const profitN = s ? Number(s.profit) : 0;

  function Th({ label, sk, col }: { label: string; sk?: SortKey; col?: ColKey }) {
    const active = col ? (colFilters[col]?.size ?? 0) > 0 : false;
    return (
      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 whitespace-nowrap">
        <div className="flex items-center gap-1">
          {sk ? (
            <button onClick={() => toggleSort(sk)} className="flex items-center gap-0.5 hover:text-gray-900">
              {label}
              <span className="text-gray-400">
                {sortKey === sk ? (sortDir === "asc" ? " ↑" : " ↓") : " ↕"}
              </span>
            </button>
          ) : label}
          {col && (
            <div className="relative">
              <button
                onClick={() => setOpenFilter(openFilter === col ? null : col)}
                className={`text-xs px-0.5 rounded ${active ? "text-blue-600 font-bold" : "text-gray-300 hover:text-gray-500"}`}
                title="값 필터"
              >⊟</button>
              {openFilter === col && (
                <ColFilter
                  col={col} rows={rows}
                  active={colFilters[col] ?? new Set()}
                  onClose={() => setOpenFilter(null)}
                  onSelect={(vals) => setColFilters((prev) => ({ ...prev, [col]: vals }))}
                />
              )}
            </div>
          )}
        </div>
      </th>
    );
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">🛒 네이버 스마트스토어 운영 패널</h1>

      {/* 기간 선택 */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              onClick={() => setDays(p.days)}
              className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
                days === p.days
                  ? "bg-green-600 text-white"
                  : "bg-white border border-gray-300 text-gray-700 hover:bg-gray-50"
              }`}
            >{p.label}</button>
          ))}
        </div>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-3 py-1.5 text-sm rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >{syncing ? "동기화 중…" : "🔄 동기화"}</button>
        {data && (
          <span className="text-xs text-gray-400">
            {data.period.from} ~ {data.period.to}
            {data.ad_ref_date && ` (광고비 기준일: ${data.ad_ref_date})`}
          </span>
        )}
      </div>

      {/* 디스플레이(GFA) 광고비 신선도 배지 + 업로드 */}
      {(() => {
        const ago = daysSince(gfa?.date_to);
        const stale = ago == null || ago >= 2;   // 어제(1일)까지는 정상(당일 데이터는 미제공)
        return (
          <div className={`flex flex-wrap items-center gap-3 mb-6 px-4 py-3 rounded-lg border ${
            stale ? "border-red-300 bg-red-50" : "border-green-200 bg-green-50"
          }`}>
            <span className="text-sm font-medium text-gray-700">디스플레이 광고비(GFA)</span>
            {gfa?.date_to ? (
              <span className={`text-sm ${stale ? "text-red-600 font-semibold" : "text-green-700"}`}>
                마지막 업로드: {gfa.date_to}
                {ago != null && ago > 0 && ` (${ago}일 전)`}
                {stale ? " ⚠️ 업데이트 필요" : " ✓ 최신"}
              </span>
            ) : (
              <span className="text-sm text-red-600 font-semibold">데이터 없음 ⚠️ CSV 업로드 필요</span>
            )}
            <button
              onClick={() => gfaFileRef.current?.click()}
              disabled={gfaUploading}
              className="ml-auto px-3 py-1.5 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >{gfaUploading ? "업로드 중…" : "📤 CSV 업로드"}</button>
            <input
              ref={gfaFileRef} type="file" accept=".csv"
              onChange={handleGfaUpload} className="hidden"
            />
            {gfaMsg && <span className="w-full text-xs text-gray-600">{gfaMsg}</span>}
            <span className="w-full text-xs text-gray-400">
              네이버 광고주센터 → 보고서 → 광고비 보고서 CSV 다운로드 후 업로드 (API 미제공으로 수동)
            </span>
          </div>
        );
      })()}

      {/* 요약 카드 */}
      {s && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          <SummaryCard label="총매출" value={won(s.revenue)} />
          <SummaryCard label="PG수수료" value={won(s.fee)} />
          <SummaryCard label="원가" value={won(s.cost)} />
          <SummaryCard label="광고비" value={won(s.ad_spend)} sub="검색+디스플레이 · 상품별 미배분" />
          <SummaryCard label="배송비" value={won(s.shipping)} />
          <SummaryCard
            label="이익"
            value={won(s.profit)}
            highlight={profitN >= 0 ? "blue" : "red"}
          />
          <SummaryCard
            label="이익률"
            value={s.profit_rate ? pct(s.profit_rate) : "—"}
            highlight={profitN >= 0 ? "blue" : "red"}
          />
          <SummaryCard
            label="검색광고 전환매출"
            value={won(s.sa_conv_revenue)}
            sub={s.sa_conv_from ? `구매·직접+간접 · ${s.sa_conv_from}~${s.sa_conv_to}` : "구매 기준(직접+간접)"}
          />
          <SummaryCard
            label="검색광고 RoAS"
            value={s.sa_roas ? `${Number(s.sa_roas).toFixed(2)}x` : "—"}
            sub={s.sa_conv_from ? `전환매출÷광고비 · 전환 ${s.sa_conv_from}~${s.sa_conv_to}` : "디스플레이 제외"}
          />
        </div>
      )}

      {/* 상태 */}
      {loading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {/* 상품별 테이블 */}
      {!loading && sorted.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="min-w-full bg-white text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <Th label="상품명" sk="product_name" />
                <Th label="총매출" sk="revenue" col="revenue" />
                <Th label="이익" sk="profit" col="profit" />
                <Th label="이익률" sk="profit_rate" col="profit_rate" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sorted.map((r, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-3 py-2 max-w-xs">
                    <div className="text-gray-900 truncate" title={r.product_name}>{r.product_name}</div>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{won(r.revenue)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${profitColor(r.profit)}`}>{won(r.profit)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${profitColor(r.profit)}`}>{pct(r.profit_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-xs text-gray-400 px-3 py-2 border-t border-gray-100">
            * 이익 = 매출 − PG수수료 − 원가 − 배송비 (광고비는 요약 카드에서 총합 차감)
          </div>
        </div>
      )}
      {!loading && data && sorted.length === 0 && (
        <p className="text-sm text-gray-500">해당 기간에 주문 데이터가 없습니다.</p>
      )}
    </div>
  );
}
