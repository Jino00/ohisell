// CoupangOps.tsx — 🔧 쿠팡 운영 패널
// 회사(오픽스/오하이테크)·기간별 매출 현황 + 상품명별 상세 (채널타입 필터).
// D-3: 사실/지표 정리만 — 전략 추천 없음.
import { useState, useEffect, useCallback } from "react";
import { fetchSalesSummary, type SalesSummary, type SalesProductRow } from "../lib/api";

const COMPANIES = [
  { value: "ALL", label: "전체" },
  { value: "오픽스", label: "오픽스" },
  { value: "오하이테크", label: "오하이테크" },
];

const PERIODS = [
  { label: "어제", days: 1 },
  { label: "7일", days: 7 },
  { label: "15일", days: 15 },
  { label: "30일", days: 30 },
];

const CHANNEL_TYPES = ["전체", "Wing", "로켓그로스", "로켓배송"] as const;
type ChannelType = (typeof CHANNEL_TYPES)[number];

type SortKey = "product_name" | "revenue" | "ad_spend" | "conv_revenue" | "roas";
type SortDir = "asc" | "desc";

function won(s: string | null | undefined) {
  if (s == null) return "—";
  const n = Math.round(Number(s));
  return `${n.toLocaleString("ko-KR")}원`;
}
function roas(s: string | null | undefined) {
  if (s == null) return "—";
  return `${Number(s).toFixed(2)}x`;
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
  const [hideZero, setHideZero] = useState<Set<string>>(new Set());

  const [data, setData] = useState<SalesSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (c: string, d: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchSalesSummary(c, d));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(company, days);
  }, [company, days, load]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  function SortIcon({ col }: { col: SortKey }) {
    if (sortKey !== col) return <span className="ml-1 text-gray-300">↕</span>;
    return <span className="ml-1 text-blue-500">{sortDir === "desc" ? "↓" : "↑"}</span>;
  }

  const filtered: SalesProductRow[] = (data?.by_product ?? []).filter((row) => {
    const matchCh = channelFilter === "전체" || row.channel_type === channelFilter;
    const q = search.toLowerCase();
    const matchQ =
      !q ||
      row.product_name.toLowerCase().includes(q) ||
      row.option_name.toLowerCase().includes(q);
    return matchCh && matchQ;
  }).filter((row) => {
    if (hideZero.has("revenue") && Number(row.revenue) === 0) return false;
    if (hideZero.has("ad_spend") && Number(row.ad_spend) === 0) return false;
    if (hideZero.has("conv_revenue") && Number(row.conv_revenue) === 0) return false;
    if (hideZero.has("roas") && (row.roas == null || Number(row.roas) === 0)) return false;
    return true;
  }).sort((a, b) => {
    const mul = sortDir === "desc" ? -1 : 1;
    if (sortKey === "product_name") {
      return mul * (`${a.product_name},${a.option_name}`).localeCompare(`${b.product_name},${b.option_name}`, "ko");
    }
    const av = Number(sortKey === "roas" ? (a.roas ?? 0) : a[sortKey]);
    const bv = Number(sortKey === "roas" ? (b.roas ?? 0) : b[sortKey]);
    return mul * (av - bv);
  });

  const s = data?.summary;

  return (
    <div>
      {/* ── 헤더 ── */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold text-gray-900">🔧 쿠팡 운영 패널</h2>
          {data && (
            <p className="text-xs text-gray-400 mt-0.5">
              {data.period.from} ~ {data.period.to}
            </p>
          )}
        </div>

        {/* 기간 버튼 */}
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              onClick={() => setDays(p.days)}
              className={`px-3 py-1.5 rounded text-sm font-medium ${
                days === p.days
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── 회사 탭 ── */}
      <div className="flex gap-1 mb-4 border-b border-gray-200 pb-0">
        {COMPANIES.map((c) => (
          <button
            key={c.value}
            onClick={() => setCompany(c.value)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              company === c.value
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 rounded px-4 py-2 text-sm mb-4">{error}</div>
      )}

      {/* ── 요약 카드 ── */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        <SummaryCard label="총 매출" value={loading ? "…" : won(s?.revenue)} />
        <SummaryCard label="광고비" value={loading ? "…" : won(s?.ad_spend)} />
        <SummaryCard label="광고 전환 매출" value={loading ? "…" : won(s?.conv_revenue)} />
        <SummaryCard
          label="RoAS"
          value={loading ? "…" : roas(s?.roas)}
          sub={s?.roas ? "광고 전환매출 ÷ 광고비" : undefined}
        />
      </div>

      {/* ── 상품별 테이블 ── */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        {/* 테이블 필터 헤더 */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50 flex-wrap">
          <span className="text-sm font-medium text-gray-700">상품별 현황</span>
          {/* 채널 필터 */}
          <div className="flex gap-1">
            {CHANNEL_TYPES.map((ct) => (
              <button
                key={ct}
                onClick={() => setChannelFilter(ct)}
                className={`px-2.5 py-1 rounded text-xs font-medium ${
                  channelFilter === ct
                    ? "bg-gray-800 text-white"
                    : "bg-white border border-gray-300 text-gray-600 hover:bg-gray-100"
                }`}
              >
                {ct}
              </button>
            ))}
          </div>
          {/* 0 숨기기 토글 */}
          <div className="flex gap-1 border-l border-gray-200 pl-3">
            {(
              [
                { key: "revenue", label: "매출 0" },
                { key: "ad_spend", label: "광고비 0" },
                { key: "conv_revenue", label: "전환매출 0" },
                { key: "roas", label: "RoAS 0" },
              ] as { key: string; label: string }[]
            ).map(({ key, label }) => {
              const active = hideZero.has(key);
              return (
                <button
                  key={key}
                  onClick={() => {
                    setHideZero((prev) => {
                      const next = new Set(prev);
                      active ? next.delete(key) : next.add(key);
                      return next;
                    });
                  }}
                  className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                    active
                      ? "bg-red-500 text-white"
                      : "bg-white border border-gray-300 text-gray-500 hover:bg-gray-100"
                  }`}
                  title={active ? `${label} 행 숨기는 중 — 클릭하여 다시 표시` : `${label} 행 숨기기`}
                >
                  {active ? `${label} 숨김` : `${label}`}
                </button>
              );
            })}
          </div>
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
              <th
                className="px-4 py-2 text-left cursor-pointer hover:text-gray-800 select-none"
                onClick={() => toggleSort("product_name")}
              >
                상품명<SortIcon col="product_name" />
              </th>
              <th className="px-3 py-2 text-center">채널</th>
              <th
                className="px-3 py-2 text-right cursor-pointer hover:text-gray-800 select-none"
                onClick={() => toggleSort("revenue")}
              >
                총 매출<SortIcon col="revenue" />
              </th>
              <th
                className="px-3 py-2 text-right cursor-pointer hover:text-gray-800 select-none"
                onClick={() => toggleSort("ad_spend")}
              >
                광고비<SortIcon col="ad_spend" />
              </th>
              <th
                className="px-3 py-2 text-right cursor-pointer hover:text-gray-800 select-none"
                onClick={() => toggleSort("conv_revenue")}
              >
                광고 전환매출<SortIcon col="conv_revenue" />
              </th>
              <th
                className="px-3 py-2 text-right cursor-pointer hover:text-gray-800 select-none"
                onClick={() => toggleSort("roas")}
              >
                RoAS<SortIcon col="roas" />
              </th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  로딩 중…
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  {data?.by_product.length === 0
                    ? "데이터 없음 — 동기화 후 조회하세요"
                    : "검색 결과 없음"}
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
                      {row.option_name && (
                        <span className="text-gray-400">, {row.option_name}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                        row.channel_type === "Wing"
                          ? "bg-blue-50 text-blue-700"
                          : row.channel_type === "로켓그로스"
                          ? "bg-orange-50 text-orange-700"
                          : "bg-purple-50 text-purple-700"
                      }`}
                    >
                      {row.channel_type}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-medium">{won(row.revenue)}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{won(row.ad_spend)}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{won(row.conv_revenue)}</td>
                  <td className="px-3 py-2 text-right">
                    {row.roas ? (
                      <span
                        className={
                          Number(row.roas) >= 3
                            ? "text-green-600 font-medium"
                            : Number(row.roas) >= 1
                            ? "text-gray-700"
                            : "text-red-500"
                        }
                      >
                        {roas(row.roas)}
                      </span>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
          {filtered.length > 0 && (
            <tfoot className="bg-gray-50 border-t border-gray-200 text-sm font-semibold">
              <tr>
                <td className="px-4 py-2 text-gray-600" colSpan={2}>
                  합계 ({filtered.length}개)
                </td>
                <td className="px-3 py-2 text-right">
                  {won(String(filtered.reduce((a, r) => a + Number(r.revenue), 0)))}
                </td>
                <td className="px-3 py-2 text-right">
                  {won(String(filtered.reduce((a, r) => a + Number(r.ad_spend), 0)))}
                </td>
                <td className="px-3 py-2 text-right">
                  {won(String(filtered.reduce((a, r) => a + Number(r.conv_revenue), 0)))}
                </td>
                <td className="px-3 py-2 text-right">
                  {(() => {
                    const sp = filtered.reduce((a, r) => a + Number(r.ad_spend), 0);
                    const cv = filtered.reduce((a, r) => a + Number(r.conv_revenue), 0);
                    return sp ? `${(cv / sp).toFixed(2)}x` : "—";
                  })()}
                </td>
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
