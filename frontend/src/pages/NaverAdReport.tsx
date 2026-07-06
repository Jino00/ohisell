// NaverAdReport.tsx — 네이버 SA 광고 리포트 (P1, track_naver-ad-optimization)
// 필터바(기간+비교) + KPI 8칸(+증감%) + 듀얼차트(광고비/ROAS) + 드릴다운 탭 + 3열 ROAS + BEP 표
import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  Bar,
  Line,
} from "recharts";
import {
  fetchNaverAdReport,
  fetchNaverAdBep,
  type NaverAdReport as NaverAdReportData,
  type NaverAdBepList,
  type NaverAdGrain,
  type NaverAdDrilldownRow,
  type NaverAdHourlyRow,
} from "../lib/api";

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

function daysAgo(n: number): string {
  return isoKST(new Date(Date.now() - n * 86400000));
}

function fmt(n: number | null | undefined): string {
  if (n == null) return "-";
  return n.toLocaleString("ko-KR");
}

function won(n: number | null | undefined): string {
  if (n == null) return "-";
  return `${fmt(n)}원`;
}

function pct(n: number | null | undefined, digits = 2): string {
  if (n == null) return "-";
  return `${(n * 100).toFixed(digits)}%`;
}

function roasX(n: number | null | undefined): string {
  if (n == null) return "-";
  return `${n.toFixed(2)}배`;
}

function deltaLabel(n: number | null | undefined): { text: string; cls: string } {
  if (n == null) return { text: "-", cls: "text-gray-400" };
  const sign = n > 0 ? "+" : "";
  const cls = n > 0 ? "text-green-600" : n < 0 ? "text-red-600" : "text-gray-400";
  return { text: `${sign}${n.toFixed(1)}%`, cls };
}

const GRAIN_TABS: { key: NaverAdGrain; label: string }[] = [
  { key: "date", label: "날짜" },
  { key: "campaign", label: "캠페인" },
  { key: "adgroup", label: "그룹" },
  { key: "keyword", label: "키워드" },
  { key: "hour", label: "시간대" },
];

const QUICK_RANGES = [
  { label: "지난 7일", from: daysAgo(6), to: daysAgo(0) },
  { label: "지난 14일", from: daysAgo(13), to: daysAgo(0) },
  { label: "지난 30일", from: daysAgo(29), to: daysAgo(0) },
];

function KpiCard({ label, value, delta }: { label: string; value: string; delta?: number | null }) {
  const d = delta === undefined ? null : deltaLabel(delta);
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-3">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="text-lg font-semibold text-gray-900 tabular-nums">{value}</div>
      {d && <div className={`text-xs mt-0.5 ${d.cls}`}>{d.text}</div>}
    </div>
  );
}

function isHourlyRows(_rows: NaverAdReportData["rows"], grain: NaverAdGrain): _rows is NaverAdHourlyRow[] {
  return grain === "hour";
}

function drilldownKey(row: NaverAdDrilldownRow | NaverAdHourlyRow, grain: NaverAdGrain, index: number): string {
  if (grain === "hour") return `h${(row as NaverAdHourlyRow).hour}`;
  const r = row as NaverAdDrilldownRow;
  const parts = [r.ad_date, r.campaign_id, r.adgroup_id, r.keyword_id].filter(Boolean).join("/");
  return `${parts}#${index}`;
}

export default function NaverAdReport() {
  const today = isoKST(new Date());
  const [dateFrom, setDateFrom] = useState(daysAgo(6));
  const [dateTo, setDateTo] = useState(today);
  const [compareOn, setCompareOn] = useState(false);
  const [compareFrom, setCompareFrom] = useState("");
  const [compareTo, setCompareTo] = useState("");
  const [grain, setGrain] = useState<NaverAdGrain>("date");

  const [report, setReport] = useState<NaverAdReportData | null>(null);
  const [bep, setBep] = useState<NaverAdBepList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchNaverAdReport({
        dateFrom,
        dateTo,
        grain,
        compareFrom: compareOn && compareFrom ? compareFrom : undefined,
        compareTo: compareOn && compareTo ? compareTo : undefined,
      });
      setReport(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadBep() {
    try {
      const data = await fetchNaverAdBep({ onlyActionable: true, sort: "bep_roas", limit: 50 });
      setBep(data);
    } catch {
      /* fail-soft — BEP는 보조 정보 */
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [dateFrom, dateTo, grain, compareOn, compareFrom, compareTo]);
  useEffect(() => { loadBep(); }, []);

  const kpis = report?.kpis;
  const roas3 = report?.roas_3col;
  const compare = report?.compare;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">네이버 SA 광고 리포트</h1>
        <p className="text-xs text-gray-400">D-NAO-15: 읽기 전용 리포트 코어 (캠페인 관리는 P2)</p>
      </div>

      {/* 필터바 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-gray-600">조회 기간</span>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1" />
          <span className="text-gray-400">~</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1" />
          {QUICK_RANGES.map((q) => (
            <button key={q.label} onClick={() => { setDateFrom(q.from); setDateTo(q.to); }}
              className="px-2 py-1 text-xs text-blue-600 border border-blue-200 rounded hover:bg-blue-50">
              {q.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-gray-600">
            <input type="checkbox" checked={compareOn} onChange={(e) => setCompareOn(e.target.checked)} />
            비교 기간
          </label>
          {compareOn && (
            <>
              <input type="date" value={compareFrom} onChange={(e) => setCompareFrom(e.target.value)}
                className="text-sm border border-gray-300 rounded px-2 py-1" />
              <span className="text-gray-400">~</span>
              <input type="date" value={compareTo} onChange={(e) => setCompareTo(e.target.value)}
                className="text-sm border border-gray-300 rounded px-2 py-1" />
            </>
          )}
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-600 text-sm rounded-lg p-3">{error}</div>}

      {/* KPI 8칸 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="노출수" value={fmt(kpis?.imp)} delta={compare?.deltas_pct?.imp} />
        <KpiCard label="클릭수" value={fmt(kpis?.clk)} delta={compare?.deltas_pct?.clk} />
        <KpiCard label="클릭률(CTR)" value={pct(kpis?.ctr)} />
        <KpiCard label="평균순위" value={kpis?.avg_rank != null ? kpis.avg_rank.toFixed(1) : "-"} />
        <KpiCard label="광고비" value={won(kpis?.cost)} delta={compare?.deltas_pct?.cost} />
        <KpiCard label="전환수" value={fmt(kpis?.conv_cnt)} delta={compare?.deltas_pct?.conv_cnt} />
        <KpiCard label="전환매출" value={won(kpis?.conv_amt)} delta={compare?.deltas_pct?.conv_amt} />
        <KpiCard label="ROAS(네이버)" value={roasX(kpis?.roas_naver)} delta={compare?.deltas_pct?.roas_naver} />
      </div>

      {/* 3열 ROAS */}
      {roas3 && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">3열 ROAS 대조 (D-NAO-7)</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <div className="text-xs text-gray-500">① 네이버(직+간접)</div>
              <div className="text-base font-semibold">{roasX(roas3.naver.roas)}</div>
              <div className="text-xs text-gray-400">{won(roas3.naver.revenue)}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">② 직접전환만</div>
              <div className="text-base font-semibold">{roasX(roas3.direct.roas)}</div>
              <div className="text-xs text-gray-400">{won(roas3.direct.revenue)}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">③ 실주문 대조 ({roas3.actual_order.order_count}건)</div>
              <div className="text-base font-semibold">{roasX(roas3.actual_order.roas)}</div>
              <div className="text-xs text-gray-400">{won(roas3.actual_order.revenue)}</div>
            </div>
          </div>
          <p className="text-xs text-gray-400 mt-3">{roas3.actual_order.note}</p>
        </div>
      )}

      {/* 듀얼차트: 광고비(막대) + ROAS(선) */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3">일별 광고비 & ROAS 추이</h3>
        {loading && !report ? (
          <div className="h-72 bg-gray-50 rounded animate-pulse" />
        ) : !report || report.trend.length === 0 ? (
          <div className="h-72 flex items-center justify-center text-gray-400 text-sm">데이터가 없습니다</div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={report.trend}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="ad_date" tick={{ fontSize: 12, fill: "#9ca3af" }}
                tickFormatter={(v: string) => { const d = new Date(v); return `${d.getMonth() + 1}/${d.getDate()}`; }} />
              <YAxis yAxisId="left" tick={{ fontSize: 12, fill: "#9ca3af" }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 12, fill: "#9ca3af" }}
                tickFormatter={(v: number) => v.toFixed(1)} />
              <Tooltip formatter={(v, name) => (name === "광고비" ? won(Number(v)) : roasX(Number(v)))} />
              <Legend />
              <Bar yAxisId="left" dataKey="cost" name="광고비" fill="#3b82f6" radius={[2, 2, 0, 0]} />
              <Line yAxisId="right" type="monotone" dataKey="roas_naver" name="ROAS" stroke="#22c55e" strokeWidth={2} dot={{ r: 3 }} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* 드릴다운 탭 */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="flex border-b border-gray-200">
          {GRAIN_TABS.map((t) => (
            <button key={t.key} onClick={() => setGrain(t.key)}
              className={`px-4 py-2.5 text-sm ${grain === t.key ? "border-b-2 border-blue-600 text-blue-700 font-medium" : "text-gray-500 hover:text-gray-700"}`}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="overflow-x-auto">
          {loading ? (
            <div className="p-8 text-center text-gray-400 text-sm">불러오는 중...</div>
          ) : !report || report.rows.length === 0 ? (
            <div className="p-8 text-center text-gray-400 text-sm">데이터가 없습니다</div>
          ) : isHourlyRows(report.rows, grain) ? (
            <table className="w-full">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left">시간대</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">노출수</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">클릭수</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">광고비</th>
                </tr>
              </thead>
              <tbody>
                {(report.rows as NaverAdHourlyRow[]).map((r, i) => (
                  <tr key={`h${r.hour}#${i}`} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-sm border-b border-gray-100">{r.hour}시</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{fmt(r.imp)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{fmt(r.clk)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left">
                    {grain === "date" ? "날짜" : grain === "campaign" ? "캠페인" : grain === "adgroup" ? "그룹" : "키워드"}
                  </th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">노출수</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">클릭수</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">CTR</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">광고비</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">전환매출</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">ROAS</th>
                </tr>
              </thead>
              <tbody>
                {(report.rows as NaverAdDrilldownRow[]).map((r, i) => (
                  <tr key={drilldownKey(r, grain, i)} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-sm border-b border-gray-100">
                      {grain === "date" ? r.ad_date
                        : grain === "campaign" ? `${r.campaign_id} (${r.campaign_type})`
                        : grain === "adgroup" ? `${r.campaign_id} / ${r.adgroup_id}`
                        : `${r.adgroup_id} / ${r.keyword_id}`}
                    </td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{fmt(r.imp)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{fmt(r.clk)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{pct(r.ctr)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.cost)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.conv_amt)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{roasX(r.roas_naver)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* BEP 상품별 목록 (보조 정보) */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between">
          <h3 className="text-sm font-medium text-gray-700">상품별 BEP ROAS</h3>
          {bep && <span className="text-xs text-gray-400">actionable {bep.actionable} / total {bep.total}</span>}
        </div>
        <div className="overflow-x-auto">
          {!bep ? (
            <div className="p-8 text-center text-gray-400 text-sm">불러오는 중...</div>
          ) : bep.rows.length === 0 ? (
            <div className="p-8 text-center text-gray-400 text-sm">actionable 상품이 없습니다</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-left">상품명</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">판매가</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">공헌이익</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">BEP ROAS</th>
                  <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">권장 ROAS</th>
                </tr>
              </thead>
              <tbody>
                {bep.rows.map((r, i) => (
                  <tr key={`${r.channel_product_id}#${i}`} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-sm border-b border-gray-100 max-w-xs truncate" title={r.product_name ?? ""}>
                      {r.product_name ?? r.channel_product_id}
                    </td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.selling_price)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.contribution_margin)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{roasX(r.bep_roas)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{roasX(r.target_roas)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
