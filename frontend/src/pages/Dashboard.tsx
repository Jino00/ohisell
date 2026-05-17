// Dashboard.tsx — 대시보드 페이지 (Sprint 3)
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ComposedChart,
  Bar,
  Line,
  LineChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Legend,
} from "recharts";
import {
  fetchApi,
  type KpiData,
  type TrendItem,
  type ChannelBreakdown,
  type ChannelTrendPoint,
  type ProductRanking,
} from "../lib/api";

type PeriodType = "daily" | "weekly" | "monthly";
type SortBy = "revenue" | "net_profit" | "profit_rate";

const PERIOD_LABELS: Record<PeriodType, string> = {
  daily: "일별",
  weekly: "주별",
  monthly: "월별",
};

const SORT_LABELS: Record<SortBy, string> = {
  revenue: "매출순",
  net_profit: "이익순",
  profit_rate: "이익률순",
};

const PIE_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#6366f1"];

function formatKRW(value: number): string {
  return Math.round(value).toLocaleString("ko-KR");
}

function formatCompact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 100_000_000) return `${(value / 100_000_000).toFixed(1)}억`;
  if (abs >= 10_000) return `${(value / 10_000).toFixed(0)}만`;
  return formatKRW(value);
}

// API returns Decimal fields as strings — parse to numbers
function parseNumbers<T extends Record<string, unknown>>(obj: T): T {
  const result = { ...obj };
  for (const key of Object.keys(result)) {
    const v = result[key];
    if (typeof v === "string" && v !== "" && !isNaN(Number(v))) {
      (result as Record<string, unknown>)[key] = Number(v);
    }
  }
  return result;
}

function parseList<T extends Record<string, unknown>>(items: T[]): T[] {
  return items.map(parseNumbers);
}

// 로컬(KST) 기준 YYYY-MM-DD — toISOString()의 UTC 변환 날짜 밀림 방지
function toLocalYMD(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// 브라우저 타임존과 무관하게 KST(Asia/Seoul) 달력 날짜의 자정 Date 반환
function kstToday(): Date {
  const ymd = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date()); // "YYYY-MM-DD"
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(y, m - 1, d);
}

// D-2: 어제 종료, 오늘 제외. N일 = 어제 끝, 어제 포함 과거 N일 (KST 기준).
function quickRange(days: number) {
  const to = kstToday();
  to.setDate(to.getDate() - 1); // 어제 (KST)
  const from = new Date(to);
  from.setDate(from.getDate() - (days - 1));
  return { from: toLocalYMD(from), to: toLocalYMD(to) };
}

const QUICK_PERIODS: { label: string; days: number }[] = [
  { label: "어제", days: 1 },
  { label: "7일", days: 7 },
  { label: "14일", days: 14 },
  { label: "30일", days: 30 },
];

function getDefaultDateRange() {
  return quickRange(30);
}

function ChangeIndicator({ value }: { value: number | null | undefined }) {
  if (value == null || isNaN(value)) return <span className="text-xs text-gray-400">--</span>;
  if (value > 0) return <span className="text-xs text-green-600">&#9650; {value.toFixed(1)}%</span>;
  if (value < 0) return <span className="text-xs text-red-600">&#9660; {Math.abs(value).toFixed(1)}%</span>;
  return <span className="text-xs text-gray-400">&mdash; 0%</span>;
}

function profitRateColor(rate: number): string {
  if (rate >= 15) return "text-green-600";
  if (rate >= 5) return "text-yellow-600";
  return "text-red-600";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ChartTooltipContent({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border rounded-lg shadow-lg p-3 text-sm">
      <div className="font-medium text-gray-900 mb-1">{label}</div>
      {payload.map((p: { name: string; value: number; color: string }, i: number) => (
        <div key={i} className="flex justify-between gap-4">
          <span style={{ color: p.color }}>{p.name}</span>
          <span className="font-medium">{formatKRW(p.value)}원</span>
        </div>
      ))}
    </div>
  );
}

const TREND_LINE_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#6366f1"];
const TOTAL_COLOR = "#111827";

type MetricKey = "revenue" | "ad_spend" | "roas" | "net_profit";
type ChartRow = { date: string } & Record<string, number | string | null>;

// 채널별 추이 평탄 데이터를 지표별 차트 데이터로 피벗 (전체 라인 파생)
function buildChannelChartData(
  points: ChannelTrendPoint[],
  metric: MetricKey,
): { rows: ChartRow[]; series: string[] } {
  const dates = Array.from(new Set(points.map((p) => p.date))).sort();
  const channelNames: string[] = [];
  for (const p of points) {
    if (!channelNames.includes(p.channel_name)) channelNames.push(p.channel_name);
  }
  const idx = new Map<string, ChannelTrendPoint>();
  for (const p of points) idx.set(`${p.date}__${p.channel_name}`, p);

  const metricValue = (p: ChannelTrendPoint): number | null => {
    if (metric === "revenue") return p.revenue;
    if (metric === "ad_spend") return p.ad_spend;
    if (metric === "net_profit") return p.net_profit;
    return p.ad_spend > 0 ? (p.revenue / p.ad_spend) * 100 : null; // RoAS
  };

  const rows: ChartRow[] = dates.map((d) => {
    const row: ChartRow = { date: d };
    let sumRev = 0;
    let sumAd = 0;
    let sumNet = 0;
    let hasNet = false;
    for (const cn of channelNames) {
      const p = idx.get(`${d}__${cn}`);
      row[cn] = p ? metricValue(p) : null;
      if (p) {
        sumRev += p.revenue;
        sumAd += p.ad_spend;
        if (p.net_profit != null) {
          sumNet += p.net_profit;
          hasNet = true;
        }
      }
    }
    if (metric === "revenue") row["전체"] = sumRev;
    else if (metric === "ad_spend") row["전체"] = sumAd;
    else if (metric === "net_profit") row["전체"] = hasNet ? sumNet : null;
    else row["전체"] = sumAd > 0 ? (sumRev / sumAd) * 100 : null; // RoAS
    return row;
  });

  // 모든 값이 null인 series 제외 (예: 위탁 채널의 순이익/RoAS, 전체 포함)
  const series = channelNames.filter((cn) => rows.some((r) => r[cn] != null));
  if (rows.some((r) => r["전체"] != null)) series.push("전체");
  return { rows, series };
}

function ChannelTrendChart({
  title,
  points,
  metric,
  unit,
}: {
  title: string;
  points: ChannelTrendPoint[];
  metric: MetricKey;
  unit: "won" | "pct";
}) {
  const { rows, series } = useMemo(
    () => buildChannelChartData(points, metric),
    [points, metric],
  );
  const fmt = (v: number) =>
    unit === "pct" ? `${v.toFixed(1)}%` : `${formatKRW(v)}원`;

  return (
    <div className="bg-white border rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-700 mb-3">{title}</h3>
      {rows.length === 0 ? (
        <div className="h-64 flex items-center justify-center text-gray-400">
          데이터가 없습니다
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: "#9ca3af" }}
              tickFormatter={(v: string) => {
                const d = new Date(v);
                return `${d.getMonth() + 1}/${d.getDate()}`;
              }}
            />
            <YAxis
              tick={{ fontSize: 11, fill: "#9ca3af" }}
              tickFormatter={(v: number) =>
                unit === "pct" ? `${v}%` : formatCompact(v)
              }
            />
            <Tooltip
              formatter={(v) => (v == null ? "-" : fmt(Number(v)))}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {series.map((s, i) => (
              <Line
                key={s}
                type="monotone"
                dataKey={s}
                name={s}
                stroke={
                  s === "전체"
                    ? TOTAL_COLOR
                    : TREND_LINE_COLORS[i % TREND_LINE_COLORS.length]
                }
                strokeWidth={s === "전체" ? 2.5 : 1.5}
                dot={{ r: 2 }}
                connectNulls={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default function Dashboard() {
  const defaults = getDefaultDateRange();
  const [period, setPeriod] = useState<PeriodType>("daily");
  const [dateFrom, setDateFrom] = useState(defaults.from);
  const [dateTo, setDateTo] = useState(defaults.to);

  const [kpi, setKpi] = useState<KpiData | null>(null);
  const [trend, setTrend] = useState<TrendItem[]>([]);
  const [channels, setChannels] = useState<ChannelBreakdown[]>([]);
  const [channelTrend, setChannelTrend] = useState<ChannelTrendPoint[]>([]);
  const [products, setProducts] = useState<ProductRanking[]>([]);
  const [sortBy, setSortBy] = useState<SortBy>("revenue");
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const params = `date_from=${dateFrom}&date_to=${dateTo}`;
      const [kpiData, trendData, channelData, channelTrendData, productData] = await Promise.all([
        fetchApi<KpiData>(`/api/dashboard/kpi?${params}`),
        fetchApi<TrendItem[]>(`/api/dashboard/trend?period=${period}&${params}`),
        fetchApi<ChannelBreakdown[]>(`/api/dashboard/channel-breakdown?${params}`),
        fetchApi<ChannelTrendPoint[]>(`/api/dashboard/trend-by-channel?${params}`),
        fetchApi<ProductRanking[]>(`/api/dashboard/product-ranking?${params}&sort_by=${sortBy}&limit=20`),
      ]);
      setKpi(parseNumbers(kpiData));
      setTrend(parseList(trendData));
      setChannels(parseList(channelData));
      setChannelTrend(parseList(channelTrendData));
      setProducts(parseList(productData));
    } catch {
      // keep previous data on error
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, period, sortBy]);

  useEffect(() => {
    const timer = setTimeout(fetchAll, 300);
    return () => clearTimeout(timer);
  }, [fetchAll]);

  const totalChannelRevenue = channels.reduce((s, c) => s + c.revenue, 0);

  // 기간 요약표 파생값 (D-1/D-6: RoAS·전체행 프론트 계산)
  const sumRevenue = channels.reduce((s, c) => s + c.revenue, 0);
  const sumAdSpend = channels.reduce((s, c) => s + c.ad_spend, 0);
  const measurable = channels.filter((c) => c.net_profit != null);
  const sumNet = measurable.reduce((s, c) => s + (c.net_profit ?? 0), 0);
  const sumNetRevenue = measurable.reduce((s, c) => s + c.revenue, 0);
  const totalRoas = sumAdSpend > 0 ? (sumRevenue / sumAdSpend) * 100 : null;
  const totalRate = sumNetRevenue > 0 ? (sumNet / sumNetRevenue) * 100 : null;

  const isActiveQuick = (days: number) => {
    const r = quickRange(days);
    return dateFrom === r.from && dateTo === r.to;
  };
  const applyQuick = (days: number) => {
    const r = quickRange(days);
    setDateFrom(r.from);
    setDateTo(r.to);
  };

  return (
    <div className="max-w-7xl mx-auto">
      {/* Header */}
      <h2 className="text-2xl font-bold text-gray-900 mb-6">대시보드</h2>

      {/* KPI Cards */}
      {loading && !kpi ? (
        <div className="grid grid-cols-4 gap-4 mb-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-white border rounded-lg p-4">
              <div className="h-4 w-20 bg-gray-200 rounded animate-pulse mb-2" />
              <div className="h-6 w-32 bg-gray-200 rounded animate-pulse mb-1" />
              <div className="h-3 w-16 bg-gray-200 rounded animate-pulse" />
            </div>
          ))}
        </div>
      ) : kpi ? (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">총 매출</div>
            <div className="text-xl font-bold text-blue-600">{formatKRW(kpi.total_revenue)}원</div>
            <ChangeIndicator value={kpi.revenue_change_pct} />
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">순이익</div>
            <div className={`text-xl font-bold ${kpi.net_profit >= 0 ? "text-green-600" : "text-red-600"}`}>
              {formatKRW(kpi.net_profit)}원
            </div>
            <ChangeIndicator value={kpi.profit_change_pct} />
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">이익률</div>
            <div className={`text-xl font-bold ${profitRateColor(kpi.profit_rate)}`}>
              {kpi.profit_rate.toFixed(1)}%
            </div>
            <div className="text-xs text-gray-400">순이익 / 매출</div>
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">주문 건수</div>
            <div className="text-xl font-bold text-gray-900">{formatKRW(kpi.order_count)}건</div>
            <div className="text-xs text-gray-400">조회 기간 합산</div>
          </div>
        </div>
      ) : null}

      {/* Period selector + date range */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="flex bg-gray-100 rounded-lg p-0.5">
          {(Object.keys(PERIOD_LABELS) as PeriodType[]).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                period === p ? "bg-white shadow text-blue-700 font-medium" : "text-gray-600 hover:text-gray-900"
              }`}
            >
              {PERIOD_LABELS[p]}
            </button>
          ))}
        </div>
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm"
        />
        <div className="flex bg-gray-100 rounded-lg p-0.5">
          {QUICK_PERIODS.map((q) => (
            <button
              key={q.label}
              onClick={() => applyQuick(q.days)}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                isActiveQuick(q.days)
                  ? "bg-white shadow text-blue-700 font-medium"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              {q.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-gray-400">어제 종료 · 오늘 제외</span>
      </div>

      {/* 기간 요약표 (D-1/D-6: RoAS·전체행 프론트 파생) */}
      <div className="bg-white border rounded-lg overflow-hidden mb-6">
        <h3 className="text-sm font-medium text-gray-700 px-4 py-3 border-b">기간 요약</h3>
        {channels.length === 0 ? (
          <div className="p-8 text-center text-gray-400">데이터가 없습니다</div>
        ) : (
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">채널</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">광고비</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">RoAS</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">순이익</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">이익률</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t bg-blue-50 font-medium">
                <td className="px-4 py-3 text-sm text-gray-900">전체</td>
                <td className="px-4 py-3 text-sm text-right">{formatKRW(sumRevenue)}원</td>
                <td className="px-4 py-3 text-sm text-right text-gray-600">{formatKRW(sumAdSpend)}원</td>
                <td className="px-4 py-3 text-sm text-right">
                  {totalRoas == null ? "—" : `${totalRoas.toFixed(0)}%`}
                </td>
                <td className="px-4 py-3 text-sm text-right">
                  {totalRate == null ? "—" : `${formatKRW(sumNet)}원`}
                </td>
                <td className={`px-4 py-3 text-sm text-right ${totalRate == null ? "text-gray-400" : profitRateColor(totalRate)}`}>
                  {totalRate == null ? "—" : `${totalRate.toFixed(1)}%`}
                </td>
              </tr>
              {channels.map((c) => {
                const roas = c.ad_spend > 0 ? (c.revenue / c.ad_spend) * 100 : null;
                return (
                  <tr key={c.channel_id} className="border-t hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm text-gray-900">{c.channel_name}</td>
                    <td className="px-4 py-3 text-sm text-right font-medium">{formatKRW(c.revenue)}원</td>
                    <td className="px-4 py-3 text-sm text-right text-gray-600">{formatKRW(c.ad_spend)}원</td>
                    <td className="px-4 py-3 text-sm text-right text-gray-600">
                      {roas == null ? "—" : `${roas.toFixed(0)}%`}
                    </td>
                    <td className="px-4 py-3 text-sm text-right">
                      {c.net_profit == null ? (
                        <span className="text-gray-400">—</span>
                      ) : (
                        <span className={c.net_profit >= 0 ? "text-green-600" : "text-red-600"}>
                          {formatKRW(c.net_profit)}원
                        </span>
                      )}
                    </td>
                    <td className={`px-4 py-3 text-sm text-right ${c.profit_rate == null ? "text-gray-400" : profitRateColor(c.profit_rate)}`}>
                      {c.profit_rate == null ? "—" : `${c.profit_rate.toFixed(1)}%`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* 채널별 추이 4그래프 (D-3) */}
      <div className="grid grid-cols-2 gap-6 mb-6">
        <ChannelTrendChart title="채널별 매출 추이" points={channelTrend} metric="revenue" unit="won" />
        <ChannelTrendChart title="채널별 광고비 추이" points={channelTrend} metric="ad_spend" unit="won" />
        <ChannelTrendChart title="채널별 RoAS 추이" points={channelTrend} metric="roas" unit="pct" />
        <ChannelTrendChart title="채널별 순이익 추이" points={channelTrend} metric="net_profit" unit="won" />
      </div>

      {/* Sales Trend Chart */}
      <div className="bg-white border rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-gray-700 mb-3">매출 & 순이익 추이</h3>
        {loading && trend.length === 0 ? (
          <div className="h-80 bg-gray-50 rounded animate-pulse" />
        ) : trend.length === 0 ? (
          <div className="h-80 flex items-center justify-center text-gray-400">데이터가 없습니다</div>
        ) : (
          <ResponsiveContainer width="100%" height={320}>
            <ComposedChart data={trend}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 12, fill: "#9ca3af" }}
                tickFormatter={(v: string) => {
                  const d = new Date(v);
                  return `${d.getMonth() + 1}/${d.getDate()}`;
                }}
              />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 12, fill: "#9ca3af" }}
                tickFormatter={(v: number) => formatCompact(v)}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 12, fill: "#9ca3af" }}
                tickFormatter={(v: number) => formatCompact(v)}
              />
              <Tooltip content={<ChartTooltipContent />} />
              <Legend />
              <Bar yAxisId="left" dataKey="revenue" name="매출" fill="#3b82f6" radius={[2, 2, 0, 0]} />
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="net_profit"
                name="순이익"
                stroke="#22c55e"
                strokeWidth={2}
                dot={{ r: 3 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Two-column: Channel Pie + Channel Profit Bar */}
      <div className="grid grid-cols-2 gap-6 mb-6">
        {/* Channel Revenue Pie */}
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">채널별 매출 비중</h3>
          {channels.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-gray-400">데이터가 없습니다</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={channels}
                  dataKey="revenue"
                  nameKey="channel_name"
                  cx="50%"
                  cy="50%"
                  outerRadius={90}
                  label={(props) => {
                    const name = String(props.name ?? "");
                    const value = Number(props.value ?? 0);
                    return `${name} ${totalChannelRevenue > 0 ? ((value / totalChannelRevenue) * 100).toFixed(0) : 0}%`;
                  }}
                  labelLine={{ stroke: "#d1d5db" }}
                >
                  {channels.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(v) => `${formatKRW(Number(v ?? 0))}원`} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Channel Profit Rate Bar */}
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">채널별 이익률</h3>
          {channels.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-gray-400">데이터가 없습니다</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={channels.filter((c) => c.profit_rate != null)} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis type="number" tick={{ fontSize: 12, fill: "#9ca3af" }} tickFormatter={(v: number) => `${v}%`} />
                <YAxis
                  type="category"
                  dataKey="channel_name"
                  tick={{ fontSize: 12, fill: "#6b7280" }}
                  width={80}
                />
                <Tooltip formatter={(v) => `${Number(v ?? 0).toFixed(1)}%`} />
                <Bar dataKey="profit_rate" name="이익률" radius={[0, 4, 4, 0]}>
                  {channels.map((ch, i) => (
                    <Cell
                      key={i}
                      fill={ch.profit_rate == null ? "#d1d5db" : ch.profit_rate >= 15 ? "#22c55e" : ch.profit_rate >= 5 ? "#f59e0b" : "#ef4444"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Product Ranking Table */}
      <div className="bg-white border rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <h3 className="text-sm font-medium text-gray-700">상품 순위 (Top 20)</h3>
          <div className="flex bg-gray-100 rounded-lg p-0.5">
            {(Object.keys(SORT_LABELS) as SortBy[]).map((s) => (
              <button
                key={s}
                onClick={() => setSortBy(s)}
                className={`px-3 py-1 text-xs rounded-md transition-colors ${
                  sortBy === s ? "bg-white shadow text-blue-700 font-medium" : "text-gray-600 hover:text-gray-900"
                }`}
              >
                {SORT_LABELS[s]}
              </button>
            ))}
          </div>
        </div>

        {loading && products.length === 0 ? (
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                {["순위", "상품명", "매출", "원가", "수수료", "광고비", "순이익", "이익률"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-t">
                  {Array.from({ length: 8 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-200 rounded animate-pulse" style={{ width: `${50 + Math.random() * 40}%` }} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : products.length === 0 ? (
          <div className="p-8 text-center text-gray-400">상품 데이터가 없습니다</div>
        ) : (
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 w-12">순위</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">상품명</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">원가</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">수수료</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">광고비</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">순이익</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">이익률</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p, idx) => (
                <tr key={p.product_id} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-3 text-center text-sm text-gray-500">{idx + 1}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">
                    <div>{p.product_name}</div>
                    <div className="text-xs text-gray-400">{p.internal_sku}</div>
                  </td>
                  <td className="px-4 py-3 text-sm text-right font-medium">{formatKRW(p.revenue)}원</td>
                  <td className="px-4 py-3 text-sm text-right text-gray-600">{formatKRW(p.cost)}원</td>
                  <td className="px-4 py-3 text-sm text-right text-gray-600">{formatKRW(p.commission)}원</td>
                  <td className="px-4 py-3 text-sm text-right text-gray-600">{formatKRW(p.ad_spend)}원</td>
                  <td className={`px-4 py-3 text-sm text-right font-medium ${p.net_profit >= 0 ? "text-green-600" : "text-red-600"}`}>
                    {formatKRW(p.net_profit)}원
                  </td>
                  <td className={`px-4 py-3 text-sm text-right font-medium ${profitRateColor(p.profit_rate)}`}>
                    {p.profit_rate.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
