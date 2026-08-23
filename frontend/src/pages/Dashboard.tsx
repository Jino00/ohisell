// Dashboard.tsx — 대시보드 페이지 (Sprint 3)
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  syncRealtime,
  type KpiData,
  type TrendItem,
  type GroupedSummaryRow,
  type GroupedTrendPoint,
  type ProductRanking,
  type RocketBasis,
} from "../lib/api";
import { netScopeNote, unmappedNote } from "../lib/netScope";
import { rgFeeFactsFromRow, rgFeeNote } from "../lib/rgSettlementAxis";
import { RocketBasisToggle } from "../components/RocketBasisToggle";
import {
  ALL_REFRESH_SPECS,
  isLoginRequired,
  outcomeView,
  runStreamsRefresh,
  REQUEST_RETRY_DELAYS_MS,
  type OutcomeTone,
  type RefreshOutcome,
  type StreamRefreshSpec,
} from "../lib/streamRefresh";
import {
  loadBulkRefreshState,
  saveBulkRefreshState,
  type BulkQueueState,
} from "../lib/bulkRefreshPersistence";

type PeriodType = "daily" | "weekly" | "monthly";
type SortBy = "revenue" | "net_profit" | "profit_rate";

// '전체 갱신' 패널이 큐 하나에 대해 보여주는 상태(BulkQueueState)는 lib/bulkRefreshPersistence
// 로 옮겼다 — sessionStorage 복원 로직과 같은 파일에 있어야 "stale" 분기가 갈라지지 않는다.
// ★단계를 굳이 나눈 이유: "요청이 안 갔다"와 "Mac이 안 받는다"와 "수집하다 실패했다"는
//   처방이 전부 다른데, 예전 화면은 셋 다 똑같이 아무 말이 없었다.

/** outcomeView의 의미 tone → 화면 색. lib은 Tailwind를 모른다(프레젠테이션은 여기 몫). */
const TONE_CLASS: Record<OutcomeTone, string> = {
  progress: "text-gray-600",
  ok: "text-green-700",
  warn: "text-amber-700",
  error: "text-red-700",
};

/**
 * 큐 상태 → 화면 한 줄(아이콘·문구). 실패 사유는 자르지 않고 그대로 보인다.
 *
 * ★정착 상태(done/failed/timeout)의 문구는 **여기서 짓지 않는다**(2026-08-23 W2) —
 *   streamRefresh.outcomeView가 유일한 저자다. 종전엔 이 함수가 자기 문구를 갖고 있어서
 *   08-22 W4가 타임아웃을 세 경우로 가른 뒤에도 패널만 옛 한 문구에 머물렀고, 08-23 10:32
 *   폰 화면에서 **수집에 성공한 레인**이 「응답 없음 — Mac이 켜져 있는지 확인하세요」로 떴다.
 *   spec을 넘기지 않는 호출자(__fatal)와 이 배포 전 저장분에는 outcome이 없으므로 폴백이
 *   남아 있는데, 그 폴백도 **틀린 처방을 말하지 않는다**(사유만 그대로 보이거나 모른다고 한다).
 */
function bulkStateText(
  st: BulkQueueState | undefined,
  spec?: StreamRefreshSpec,
): { icon: string; text: string; tone: string } {
  if (!st) return { icon: "·", text: "대기", tone: "text-gray-400" };

  if (spec && (st.kind === "done" || st.kind === "failed" || st.kind === "timeout") && st.outcome) {
    const v = outcomeView(spec, st.outcome);
    // 완료 시각은 사실의 «부가»이지 처방이 아니다 — 문구 자체는 위임된 것을 그대로 쓴다.
    return { icon: v.icon, text: st.kind === "done" ? `${v.text} ${st.at}` : v.text, tone: TONE_CLASS[v.tone] };
  }

  switch (st.kind) {
    case "requesting":
      return st.retry === 0
        ? { icon: "◌", text: "요청 보내는 중…", tone: "text-gray-600" }
        : { icon: "◌", text: `요청 재시도 ${st.retry}/${st.maxRetries}…`, tone: "text-amber-700" };
    case "requested":
      return { icon: "◔", text: "요청 전달됨 — Mac 대기 중", tone: "text-gray-600" };
    case "fetching":
      return { icon: "◕", text: "Mac이 수집 중…", tone: "text-blue-700" };
    case "done":
      return { icon: "✅", text: `완료 ${st.at}`, tone: "text-green-700" };
    case "failed":
      return st.login
        ? { icon: "🔑", text: `로그인 필요 — ${st.reason}`, tone: "text-red-700" }
        : { icon: "❌", text: `실패 — ${st.reason}`, tone: "text-red-700" };
    case "timeout":
      // 폴백(이 배포 전 저장분 등) — 「Mac을 켜세요」는 참인지 모르므로 말하지 않는다.
      return {
        icon: "⚠️",
        text: "이 회차의 상세를 알 수 없습니다 — 전체 갱신을 다시 누르면 확인됩니다",
        tone: "text-amber-700",
      };
    case "stale": {
      // ★P1-7: 페이지를 벗어났다 돌아온 큐 — 폴링이 언마운트로 끊겨 지금 상태를 모른다.
      //   "수집 중"으로 계속 보이면 그것도 거짓말이므로, 추적이 끊겼다는 사실만 말한다.
      const phaseLabel =
        st.lastPhase === "fetching" ? "Mac이 수집 중" : st.lastPhase === "requested" ? "요청 전달됨" : "요청 전송";
      return {
        icon: "❓",
        text: `이 탭에서 진행 상황을 더는 추적하지 않음(창을 벗어나기 전: ${phaseLabel}) — 전체 갱신을 다시 누르면 확인됩니다`,
        tone: "text-gray-500",
      };
    }
  }
}

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
  return quickRange(1);
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

// 그룹별 추이 평탄 데이터를 지표별 차트 데이터로 피벗 (전체 라인 파생)
function buildChannelChartData(
  points: GroupedTrendPoint[],
  metric: MetricKey,
): { rows: ChartRow[]; series: string[] } {
  const dates = Array.from(new Set(points.map((p) => p.date))).sort();
  const channelNames: string[] = [];
  for (const p of points) {
    if (!channelNames.includes(p.group)) channelNames.push(p.group);
  }
  const idx = new Map<string, GroupedTrendPoint>();
  for (const p of points) idx.set(`${p.date}__${p.group}`, p);

  const metricValue = (p: GroupedTrendPoint): number | null => {
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
  points: GroupedTrendPoint[];
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
  // 로켓배송 1P 매출 축. 기본은 계산서(회계 정본) — 바꾸면 로켓 leaf 매출이 크게 달라진다
  // (실측 2026-08-04: 계산서 1,578,000 vs 판매 3,885,820). 두 축은 택일이며 합산하지 않는다.
  const [rocketBasis, setRocketBasis] = useState<RocketBasis>("settlement");
  const [dateFrom, setDateFrom] = useState(defaults.from);
  const [dateTo, setDateTo] = useState(defaults.to);

  const [kpi, setKpi] = useState<KpiData | null>(null);
  const [trend, setTrend] = useState<TrendItem[]>([]);
  const [channels, setChannels] = useState<GroupedSummaryRow[]>([]);
  const [channelTrend, setChannelTrend] = useState<GroupedTrendPoint[]>([]);
  const [products, setProducts] = useState<ProductRanking[]>([]);
  const [sortBy, setSortBy] = useState<SortBy>("revenue");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  // ★응답 순서 역전 방어 (2026-08-05 라이브 재현).
  //   조회 조건(기간·매출 축)을 바꾸면 새 요청이 나가는데, **앞 요청이 더 늦게 도착할 수 있다.**
  //   실제로 그랬다: 첫 로드가 콜드 동기화로 63초 걸리는 사이 매출 축을 토글하니 판매 축 응답
  //   (0.1초)이 먼저 와서 화면에 그려졌고, 뒤늦게 도착한 계산서 축 응답이 그것을 **덮어썼다**.
  //   증상은 "눌렀는데 안 바뀐다"지만 진짜 위험은 그게 아니다 — 라벨은 「판매」인데 값은 계산서라
  //   **틀린 축의 숫자를 맞다고 믿게 된다.**
  //   세대 카운터로 막는다: 응답이 도착했을 때 내가 최신 요청이 아니면 결과를 **버린다**.
  const fetchGen = useRef(0);

  const fetchAll = useCallback(async () => {
    const gen = ++fetchGen.current;
    setLoading(true);
    try {
      const params = `date_from=${dateFrom}&date_to=${dateTo}`;
      const rb = `rocket_basis=${rocketBasis}`;
      const [kpiData, trendData, channelData, channelTrendData, productData] = await Promise.all([
        fetchApi<KpiData>(`/api/dashboard/kpi?${params}&${rb}`),
        fetchApi<TrendItem[]>(`/api/dashboard/trend?period=${period}&${params}`),
        fetchApi<GroupedSummaryRow[]>(`/api/dashboard/channel-breakdown?${params}&${rb}`),
        fetchApi<GroupedTrendPoint[]>(`/api/dashboard/trend-by-channel?${params}&${rb}`),
        fetchApi<ProductRanking[]>(`/api/dashboard/product-ranking?${params}&sort_by=${sortBy}&limit=20`),
      ]);
      if (gen !== fetchGen.current) return;   // 뒤처진 응답 — 최신 화면을 덮지 않는다
      setKpi(parseNumbers(kpiData));
      setTrend(parseList(trendData));
      setChannels(parseList(channelData));
      setChannelTrend(parseList(channelTrendData));
      setProducts(parseList(productData));
    } catch {
      // keep previous data on error
    } finally {
      // 뒤처진 응답이 최신 요청의 로딩 표시를 끄면 "다 됐다"로 잘못 보인다
      if (gen === fetchGen.current) setLoading(false);
    }
  }, [dateFrom, dateTo, period, sortBy, rocketBasis]);

  // ★항상 **최신** fetchAll을 부르기 위한 ref (2026-08-05 라이브 재현).
  //   마운트 이펙트가 `syncAndRefresh`를 빈 의존성으로 잡고 있어, 그 클로저 안의 fetchAll은
  //   **마운트 시점의 조회 조건**(매출 축=계산서)에 고정된다. syncRealtime()이 느리게 끝난 뒤
  //   그 낡은 클로저가 실행되면, 사용자가 이미 「판매」로 바꾼 뒤인데도 **계산서 요청을 새로 쏘고**
  //   그게 최신 응답이 되어 화면을 덮는다. 세대 카운터로는 못 막는다 — 늦게 출발했으니
  //   정당하게 최신 세대다. 조건 자체가 낡은 게 문제이므로 ref로 최신 함수를 부른다.
  const fetchAllRef = useRef(fetchAll);
  useEffect(() => { fetchAllRef.current = fetchAll; }, [fetchAll]);

  const syncAndRefresh = useCallback(async () => {
    setSyncing(true);
    try { await syncRealtime(); } catch { /* fail-soft */ }
    setSyncing(false);
    fetchAllRef.current();
  }, []);

  // ── '전체 갱신' — 수동 수집 6큐를 한 번에 ────────────────────────────
  // ★왜 버튼을 하나로 합쳤나(2026-08-05 Jino): 갱신 버튼이 5개 화면에 흩어져 있어 무엇을
  //   눌러야 최신이 되는지 알 수 없었고, 하나라도 빠뜨리면 화면 숫자가 조용히 낡았다.
  // ★왜 상태 패널이 본체인가: 같은 날 실사고의 정체는 "버튼이 안 눌린다"가 아니라
  //   **어느 단계에서 죽었는지 화면이 말해주지 않는다**였다. 요청 POST 유실·Mac 데몬 다운·
  //   로그인 만료는 처방이 전부 다른데 증상이 똑같이 "아무 일도 안 일어남"으로 보였다.
  //   그래서 큐별로 단계를 보이고, **실패는 지우지 않는다**(성공만 자동으로 접는다).
  // ★P1-7: 초기값을 sessionStorage에서 복원한다 — 페이지 이동/새로고침으로 이 컴포넌트가
  //   사라져도(마운트 전이라 폴링과 무관) 마지막 결과가 화면에 남는다. 진행 중이던 단계는
  //   loadBulkRefreshState가 이미 "stale"로 바꿔서 준다(추적 끊김을 숨기지 않는다).
  const [bulkStates, setBulkStates] = useState<Record<string, BulkQueueState>>(
    () => loadBulkRefreshState()?.states ?? {},
  );
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkPanelOpen, setBulkPanelOpen] = useState<boolean>(
    () => loadBulkRefreshState()?.panelOpen ?? false,
  );
  // ★P2-4: 수집 뒤 화면 동기화(syncRealtime)가 실패해도 완전 침묵하지 않는다 — 패널에 한 줄 남긴다.
  const [bulkSyncWarning, setBulkSyncWarning] = useState<string | null>(null);
  const bulkRunSeq = useRef(0);
  const bulkCollapseTimer = useRef<number | undefined>(undefined);

  // ★P1-7: bulkStates/bulkPanelOpen이 바뀔 때마다 sessionStorage에 미러링한다(가장 작은 변경 —
  //   컴포넌트가 사라지는 시점을 따로 잡을 필요 없이, 마지막으로 반영된 상태가 항상 저장돼 있다).
  useEffect(() => {
    saveBulkRefreshState(bulkStates, bulkPanelOpen);
  }, [bulkStates, bulkPanelOpen]);

  const runBulkRefresh = useCallback(async () => {
    const gen = ++bulkRunSeq.current;
    const alive = () => gen === bulkRunSeq.current;
    window.clearTimeout(bulkCollapseTimer.current);

    setBulkRunning(true);
    setBulkPanelOpen(true);
    setBulkSyncWarning(null);
    setBulkStates(
      Object.fromEntries(
        ALL_REFRESH_SPECS.map((s) => [
          s.key,
          { kind: "requesting", retry: 0, maxRetries: REQUEST_RETRY_DELAYS_MS.length } as BulkQueueState,
        ]),
      ),
    );

    const put = (key: string, st: BulkQueueState) => {
      if (!alive()) return;
      setBulkStates((prev) => ({ ...prev, [key]: st }));
    };

    let results: Map<string, RefreshOutcome>;
    try {
      results = await runStreamsRefresh(
        ALL_REFRESH_SPECS,
        (spec, outcome) => {
          // ★outcome을 통째로 싣는다(2026-08-23 W2) — 접어서 넘기면 attemptCount·inFlight·kind가
          //   사라져 패널이 타임아웃 세 경우를 가를 수 없다(그게 08-23 10:32 폰 오보의 자리다).
          put(
            spec.key,
            outcome.state === "done"
              ? {
                  kind: "done",
                  at: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }),
                  outcome,
                }
              : outcome.state === "failed"
                ? {
                    kind: "failed",
                    reason: outcome.reason ?? "실패",
                    // ★2인자다: kind가 오면 그게 정본이다(1인자 호출은 백엔드의 기계 판독 분류를
                    //   버리고 문자열 매칭으로 되돌아가는 퇴행이었다 — W1이 없앤 결합).
                    login: isLoginRequired(outcome.reason, outcome.kind),
                    outcome,
                  }
                : { kind: "timeout", outcome },
          );
        },
        {
          onPhase: (spec, phase) => {
            // 진행 단계는 정착 전에만 흐른다 — 이미 결과가 난 큐를 되돌리지 않는다.
            put(spec.key, phase as BulkQueueState);
          },
        },
      );
    } catch (e: any) {
      // runStreamsRefresh는 스트림별로 catch하므로 여기까지 오는 건 예상 밖이다 — 삼키지 않는다.
      // ★P2-1: __fatal은 ALL_REFRESH_SPECS.map 밖의 값이라 예전엔 렌더가 안 됐다 — 아래 패널에
      //   별도 줄로 실제로 보인다(제거 대신 표시를 택함: 예상 밖 오류를 조용히 지우지 않는다).
      if (alive()) {
        setBulkRunning(false);
        setBulkStates((prev) => ({ ...prev, __fatal: { kind: "failed", reason: e?.message || "알 수 없는 오류", login: false } }));
      }
      return;
    }

    if (!alive()) return;
    setBulkRunning(false);

    // 서버측 주문 동기화는 Mac이 필요 없는 공짜 호출 — 수집이 끝난 뒤 한 번 돌려 화면까지 최신으로.
    // ★P2-4: 실패해도 완전 침묵하지 않는다 — 전건 수집 성공이어도 화면이 안 최신일 수 있으므로
    //   패널에 경고를 남기고, 그 경우 자동 접힘도 하지 않는다.
    let syncOk = true;
    try { await syncRealtime(); } catch { syncOk = false; }
    if (!alive()) return;
    if (!syncOk) {
      setBulkSyncWarning(
        "화면 동기화 실패 — 방금 수집한 데이터가 화면에 아직 반영되지 않았을 수 있습니다. '새로고침'을 다시 눌러 확인하세요.",
      );
    }
    fetchAllRef.current();

    // ★전건 성공일 때만 패널을 접는다. 하나라도 실패하면 남긴다 — 사라지는 실패가 이 사고의 본질이었다.
    const allDone = ALL_REFRESH_SPECS.every((s) => results.get(s.key)?.state === "done");
    if (allDone && syncOk) {
      bulkCollapseTimer.current = window.setTimeout(() => {
        if (alive()) setBulkPanelOpen(false);
      }, 6000);
    }
  }, []);

  useEffect(() => () => window.clearTimeout(bulkCollapseTimer.current), []);

  // 접속/마운트 시 1회 실시간 동기화 후 데이터 로드
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { syncAndRefresh(); }, []);

  useEffect(() => {
    const timer = setTimeout(fetchAll, 300);
    return () => clearTimeout(timer);
  }, [fetchAll]);

  // 원가 미상 매출 — 사실(unmapped_revenue)은 서버가 주고, 부풀림 금액은 여기서 **추정**한다.
  // ★추정을 서버 손익 숫자에 섞지 않는 이유: 원가를 추정해 채우는 순간 "실측 원가"와
  //   구분이 사라진다(LESSONS #117). 화면에서만, 추정이라고 밝히고 보여준다.
  const unmappedNotice = (() => {
    const unmapped = trend.reduce((s, t) => s + Number(t.unmapped_revenue ?? 0), 0);
    if (unmapped <= 0) return null;
    const revenue = trend.reduce((s, t) => s + Number(t.revenue ?? 0), 0);
    const cost = trend.reduce((s, t) => s + Number(t.cost ?? 0), 0);
    const mappedRevenue = revenue - unmapped;
    if (mappedRevenue <= 0 || cost <= 0) return null;   // 비교군이 없으면 추정하지 않는다
    const ratio = cost / mappedRevenue;
    // 모든 축이 VAT 포함이므로 순이익 영향은 빠진 원가 ÷ 1.1
    return {
      revenue: unmapped,
      overstated: Math.round((unmapped * ratio) / 1.1),
      costPct: ratio * 100,
    };
  })();

  // 서버가 total/company/leaf 계층 행을 내려줌
  const leafRows = channels.filter((c) => c.kind === "leaf");
  const leafTotalRevenue = leafRows.reduce((s, c) => s + c.revenue, 0);
  const roasOf = (rev: number, ad: number): number | null =>
    ad > 0 ? (rev / ad) * 100 : null;
  const shortLabel = (label: string) =>
    label.includes(" · ") ? label.split(" · ").slice(1).join(" · ") : label;
  const leafChart = leafRows.map((r) => ({ ...r, name: r.label }));
  const leafPie = leafRows
    .filter((r) => r.revenue > 0)
    .map((r) => ({ ...r, name: shortLabel(r.label) }));

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
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-900">대시보드</h2>
        <div className="flex items-center gap-2">
          {/* ★'전체 갱신'과 '새로고침'은 다른 일이다: 전자는 Mac 페처를 깨워 **원천에서 새로
              가져오고**(수분), 후자는 이미 들어온 데이터를 다시 그린다(수초). 라벨만으로는
              구분이 안 되므로 title로 명시한다. */}
          <button
            onClick={runBulkRefresh}
            disabled={bulkRunning}
            title="쿠팡 수집 6큐를 모두 갱신합니다(Mac 페처가 원천에서 새로 가져옴, 수 분 소요)"
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
          >
            <span className={bulkRunning ? "animate-spin" : ""}>⟳</span>
            {bulkRunning ? "전체 갱신 중…" : "전체 갱신"}
          </button>
          <button
            onClick={syncAndRefresh}
            disabled={syncing}
            title="화면 데이터만 다시 불러옵니다(수집은 하지 않음)"
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            <span className={syncing ? "animate-spin" : ""}>🔄</span>
            {syncing ? "동기화 중…" : "새로고침"}
          </button>
        </div>
      </div>

      {/* 전체 갱신 진행 패널 — ★실패는 자동으로 사라지지 않는다(전건 성공일 때만 접힌다).
          "잠깐 떴다 사라진 에러"는 없는 에러와 같고, 그게 2026-08-05 사고의 인지 실패였다. */}
      {bulkPanelOpen && (
        <div className="mb-6 rounded-lg border border-gray-200 bg-white px-4 py-3">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-semibold text-gray-900">
              전체 갱신 {bulkRunning ? "진행 중" : "결과"}
            </div>
            <button
              onClick={() => setBulkPanelOpen(false)}
              className="text-xs text-gray-500 hover:text-gray-800 underline"
            >
              닫기
            </button>
          </div>
          <ul className="space-y-1">
            {ALL_REFRESH_SPECS.map((spec) => {
              const v = bulkStateText(bulkStates[spec.key], spec);
              return (
                <li key={spec.key} className="flex items-start gap-2 text-sm">
                  <span className="shrink-0 w-5 text-center">{v.icon}</span>
                  <span className="shrink-0 w-40 text-gray-700">
                    {spec.label}
                    <span className="text-gray-400 text-xs"> · {spec.account.replace(/\(.*\)/, "")}</span>
                  </span>
                  <span className={`min-w-0 ${v.tone}`}>{v.text}</span>
                </li>
              );
            })}
            {/* ★P2-1: 예상 밖 오류(__fatal)는 ALL_REFRESH_SPECS 밖의 값이라 위 목록엔 안 뜬다 —
                여기서 별도로 표시해 조용히 사라지지 않게 한다. */}
            {bulkStates.__fatal && (
              <li className="flex items-start gap-2 text-sm">
                <span className="shrink-0 w-5 text-center">{bulkStateText(bulkStates.__fatal).icon}</span>
                <span className="shrink-0 w-40 text-gray-700">전체 갱신 자체 오류</span>
                <span className={`min-w-0 ${bulkStateText(bulkStates.__fatal).tone}`}>
                  {bulkStateText(bulkStates.__fatal).text}
                </span>
              </li>
            )}
          </ul>
          {bulkSyncWarning && (
            <div className="mt-2 text-xs text-amber-700">⚠️ {bulkSyncWarning}</div>
          )}
          {!bulkRunning && (
            <div className="mt-2 text-xs text-gray-500">
              실패한 항목은 다시 <b>전체 갱신</b>을 누르면 재시도됩니다. 로그인 필요 표시가 뜨면
              Mac의 해당 Chrome 창에서 로그인한 뒤 누르세요.
            </div>
          )}
        </div>
      )}

      {/* 원가 미상 경고 — 연결맵에 들어가야만 보이던 구멍을 손익 화면에 상설로 올린다.
          ★순이익 숫자는 바꾸지 않는다. 부풀림 금액은 '추정'임을 문구로 명시한다. */}
      {unmappedNotice && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <div className="text-sm text-amber-900">
            <b>원가를 모르는 매출이 {formatKRW(unmappedNotice.revenue)}원</b> 있습니다
            {" "}— 이 매출은 원가가 <b>0원으로 계산</b>돼 있어, 순이익이 실제보다{" "}
            <b>약 {formatKRW(unmappedNotice.overstated)}원 높게</b> 보입니다(추정).
          </div>
          <div className="mt-1 text-xs text-amber-700">
            상품 연결이 안 됐거나 원가가 입력되지 않은 주문입니다. 부풀림 금액은 같은 기간
            연결된 상품의 원가율 {unmappedNotice.costPct.toFixed(1)}%를 적용한 <b>추정치</b>이며,
            대시보드의 순이익 숫자에는 반영돼 있지 않습니다.
            {" "}
            <a href="/product-connection-map" className="underline font-medium">
              상품 연결맵에서 연결하기 →
            </a>
          </div>
        </div>
      )}

      {/* KPI Cards */}
      {loading && !kpi ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mb-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-white border rounded-lg p-4">
              <div className="h-4 w-20 bg-gray-200 rounded animate-pulse mb-2" />
              <div className="h-6 w-32 bg-gray-200 rounded animate-pulse mb-1" />
              <div className="h-3 w-16 bg-gray-200 rounded animate-pulse" />
            </div>
          ))}
        </div>
      ) : kpi ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mb-6">
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
            {kpi.net_scope === "partial" && (kpi.net_floor_ad ?? 0) > 0 ? (
              <div
                className="text-xs text-amber-600"
                title="손익을 못 재는 채널(로켓배송 계산서 축)의 광고비만 비용으로 반영했다 — 이 값은 실제 손익의 하한이다"
              >
                ⚠️ 하한 — 미측정 광고비 {formatKRW(kpi.net_floor_ad ?? 0)}원 반영
              </div>
            ) : (
              <ChangeIndicator value={kpi.profit_change_pct} />
            )}
          </div>
          <div className="bg-white border rounded-lg p-4">
            <div className="text-sm text-gray-500">이익률</div>
            <div className={`text-xl font-bold ${profitRateColor(kpi.profit_rate)}`}>
              {kpi.profit_rate.toFixed(1)}%
            </div>
            <div className="text-xs text-gray-400">순이익 / 손익을 잰 매출</div>
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
        <RocketBasisToggle value={rocketBasis} onChange={setRocketBasis} rows={channels} />
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
          <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">채널</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">제품매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">배송비매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">총매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">광고비</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">RoAS</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">순이익</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">이익률</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((c, i) => {
                const roas = roasOf(c.revenue, c.ad_spend);
                const rowCls =
                  c.kind === "total"
                    ? "bg-blue-50 font-semibold"
                    : c.kind === "company"
                    ? "bg-gray-50 font-medium"
                    : "hover:bg-gray-50";
                const nameCls =
                  c.kind === "leaf"
                    ? "px-4 py-3 text-sm text-gray-600 pl-10"
                    : "px-4 py-3 text-sm text-gray-900";
                const prodRev = Number(c.product_revenue ?? 0);
                const shipRev = Number(c.shipping_revenue ?? 0);
                // 판정은 `lib/netScope`에 있다 — 순수 함수라 테스트가 붙는다(적대 리뷰 1R P2).
                const scopeNote = netScopeNote(c);
                const costNote = unmappedNote(c);
                // RG 행만 채워져 온다 — 정산공제가 어느 축이고 무엇을 근거로 하는지
                // (계약 CONTRACT_rg_sales_date_axis §4 ⓑⓒⓓⓔ). 다른 행은 null이라 안 뜬다.
                const feeNote = rgFeeNote(rgFeeFactsFromRow(c));
                return (
                  <tr key={`${c.kind}-${c.label}-${i}`} className={`border-t ${rowCls}`}>
                    <td className={nameCls}>
                      {c.kind === "leaf" ? shortLabel(c.label) : c.label}
                    </td>
                    <td className="px-4 py-3 text-sm text-right text-gray-700">{formatKRW(prodRev)}원</td>
                    <td className="px-4 py-3 text-sm text-right text-gray-500">
                      {shipRev === 0 ? <span className="text-gray-300">—</span> : `${formatKRW(shipRev)}원`}
                    </td>
                    <td className="px-4 py-3 text-sm text-right">{formatKRW(c.revenue)}원</td>
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
                      {scopeNote && (
                        <div className="text-xs text-amber-600" title={scopeNote.title}>
                          {scopeNote.text}
                        </div>
                      )}
                      {feeNote && (
                        <div className="text-xs text-gray-500" title={feeNote.title}>
                          {feeNote.text}
                        </div>
                      )}
                    </td>
                    <td className={`px-4 py-3 text-sm text-right ${c.profit_rate == null ? "text-gray-400" : profitRateColor(c.profit_rate)}`}>
                      {c.profit_rate == null ? "—" : `${c.profit_rate.toFixed(1)}%`}
                      {costNote && (
                        <div className="text-xs text-amber-600" title={costNote.title}>
                          {costNote.text}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {/* 채널별 추이 4그래프 (D-3) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 lg:gap-6 mb-6">
        <ChannelTrendChart title="그룹별 매출 추이" points={channelTrend} metric="revenue" unit="won" />
        <ChannelTrendChart title="그룹별 광고비 추이" points={channelTrend} metric="ad_spend" unit="won" />
        <ChannelTrendChart title="그룹별 RoAS 추이" points={channelTrend} metric="roas" unit="pct" />
        <ChannelTrendChart title="그룹별 순이익 추이" points={channelTrend} metric="net_profit" unit="won" />
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
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 lg:gap-6 mb-6">
        {/* Channel Revenue Pie */}
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">그룹별 매출 비중</h3>
          {leafPie.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-gray-400">데이터가 없습니다</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={leafPie}
                  dataKey="revenue"
                  nameKey="name"
                  cx="50%"
                  cy="45%"
                  outerRadius={80}
                  label={(props) => {
                    const value = Number(props.value ?? 0);
                    const pct = leafTotalRevenue > 0 ? (value / leafTotalRevenue) * 100 : 0;
                    return pct >= 6 ? `${pct.toFixed(0)}%` : "";
                  }}
                  labelLine={false}
                >
                  {leafPie.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(v) => `${formatKRW(Number(v ?? 0))}원`} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Channel Profit Rate Bar */}
        <div className="bg-white border rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">그룹별 이익률</h3>
          {leafChart.filter((c) => c.profit_rate != null).length === 0 ? (
            <div className="h-64 flex items-center justify-center text-gray-400">데이터가 없습니다</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={leafChart.filter((c) => c.profit_rate != null)} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis type="number" tick={{ fontSize: 12, fill: "#9ca3af" }} tickFormatter={(v: number) => `${v}%`} />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  width={150}
                />
                <Tooltip formatter={(v) => `${Number(v ?? 0).toFixed(1)}%`} />
                <Bar dataKey="profit_rate" name="이익률" radius={[0, 4, 4, 0]}>
                  {leafChart.filter((c) => c.profit_rate != null).map((ch, i) => (
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
          <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50">
              <tr>
                {["순위", "상품명", "제품매출", "배송비매출", "총매출", "원가", "수수료", "광고비", "순이익", "이익률"].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-t">
                  {Array.from({ length: 10 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-200 rounded animate-pulse" style={{ width: `${50 + Math.random() * 40}%` }} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        ) : products.length === 0 ? (
          <div className="p-8 text-center text-gray-400">상품 데이터가 없습니다</div>
        ) : (
          <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 w-12">순위</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">상품명</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">제품매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">배송비매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">총매출</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">원가</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">수수료</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">광고비</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">순이익</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500">이익률</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p, idx) => {
                const prodRev = Number(p.product_revenue ?? 0);
                const shipRev = Number(p.shipping_revenue ?? 0);
                return (
                <tr key={p.product_id} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-3 text-center text-sm text-gray-500">{idx + 1}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">
                    <div>{p.product_name}</div>
                    <div className="text-xs text-gray-400">{p.internal_sku}</div>
                  </td>
                  <td className="px-4 py-3 text-sm text-right text-gray-700">{formatKRW(prodRev)}원</td>
                  <td className="px-4 py-3 text-sm text-right text-gray-500">
                    {shipRev === 0 ? <span className="text-gray-300">—</span> : `${formatKRW(shipRev)}원`}
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
                );
              })}
            </tbody>
          </table>
          <p className="px-4 py-3 text-xs text-gray-400 border-t">
            * 상품별 순이익에는 <b>월 고정비(3PL 입고·보관·항공도선·합포장)가 빠져 있습니다</b> —
            재고·입고 기반이라 상품에 배분할 근거가 없어 채널 단위로만 반영합니다.
            따라서 상품별 순이익 합계는 채널 순이익보다 그만큼 큽니다.
          </p>
          </div>
        )}
      </div>
    </div>
  );
}
