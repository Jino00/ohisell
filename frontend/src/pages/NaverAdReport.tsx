// NaverAdReport.tsx — 네이버 SA 광고 리포트 (P1, track_naver-ad-optimization)
// 필터바(기간+비교, 비교 체크→직전 동일기간 자동채움) + MOP 픽셀차용 KPI 스트립 8칸(광고비/노출/클릭/전환/
// 전환매출/CPC/CPA/ROAS, +증감%) + 이중축 라인차트(지표선택+기간단위 토글) + 드릴다운 탭 + 3열 ROAS + BEP 표
import { useEffect, useRef, useState } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
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
  type NaverAdKpis,
  type NaverAdTrendRow,
} from "../lib/api";
import { isoKST, num, won, pctFromFraction, roasX, NO_DATA } from "../lib/format";
import { LayerNav } from "../components/ui";

function daysAgo(n: number): string {
  return isoKST(new Date(Date.now() - n * 86400000));
}

// ── T3: KPI 스트립 + 이중축 차트 (MOP `15_report_campaign.png`/`15b` 픽셀 차용) ──

// MOP 관례: 증가=▲빨강 / 감소=▼파랑이 기본값. 단 비용성 지표(비용·CPC·CPA)는 증가=악화이므로
// invert=true를 줘서 색만 반전(화살표 방향은 실제 증감 그대로 유지 — 방향 표기와 호전/악화 색을 분리).
function mopDelta(n: number | null | undefined, invert = false): { text: string; cls: string } {
  if (n == null) return { text: NO_DATA, cls: "text-gray-400" };
  if (n === 0) return { text: "0.0%", cls: "text-gray-400" };
  const up = n > 0;
  const isRed = invert ? !up : up;
  const cls = isRed ? "text-red-600" : "text-blue-600";
  const arrow = up ? "▲" : "▼";
  return { text: `${arrow}${Math.abs(n).toFixed(1)}%`, cls };
}

// 백엔드 ad_report.py의 _delta_pct와 동일 규칙(prev가 0/None이면 None) — CPC·CPA는 백엔드
// deltas_pct에 없는 파생값이라 프론트에서 같은 공식으로 계산(원칙: 합산 후 계산, 행별 평균 아님).
function localDeltaPct(cur: number | null | undefined, prev: number | null | undefined): number | null {
  if (cur == null || prev == null || !prev) return null;
  return ((cur - prev) / prev) * 100;
}

// CPA는 NaverAdKpis에 없는 파생 필드(백엔드 미제공) — 기간 합산 cost/conv_cnt로 프론트 계산.
function deriveCpa(k: NaverAdKpis | null | undefined): number | null {
  if (!k || !k.conv_cnt) return null;
  return k.cost / k.conv_cnt;
}

// YYYY-MM-DD 문자열을 순수 날짜 연산(UTC 고정)으로 다뤄 로컬 타임존 off-by-one을 피함.
function parseISODate(d: string): Date {
  const [y, m, dd] = d.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, dd));
}
function formatISODate(d: Date): string {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}
// "직전 동일 길이 기간" 자동 계산 — 비교기간 체크박스를 켤 때 1회 채워줌(수동 수정은 그대로 허용).
function prevPeriod(from: string, to: string): { from: string; to: string } {
  const f = parseISODate(from);
  const t = parseISODate(to);
  const days = Math.round((t.getTime() - f.getTime()) / 86400000) + 1;
  const prevTo = new Date(f.getTime() - 86400000);
  const prevFrom = new Date(prevTo.getTime() - (days - 1) * 86400000);
  return { from: formatISODate(prevFrom), to: formatISODate(prevTo) };
}

// 'YYYY-MM-DD' 문자열을 직접 슬라이스로 파싱 — new Date('YYYY-MM-DD')는 UTC 자정으로
// 해석되어(parseISODate와 동일 함정) 로컬 타임존이 UTC보다 앞서면(KST=UTC+9) 라벨이
// 하루 당겨져 보일 수 있다. 이 파일의 다른 naive-KST 처리 관례(parseISODate 등)와 동일하게
// 문자열을 직접 분해해 로컬 타임존 영향을 받지 않게 한다.
function shortDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${m}/${d}`;
}

type ChartMetricKey = "cost" | "imp" | "clk" | "conv_cnt" | "conv_amt" | "cpc" | "cpa" | "roas_naver";

const METRIC_META: Record<ChartMetricKey, { label: string; format: (n: number | null | undefined) => string }> = {
  cost: { label: "광고비", format: won },
  imp: { label: "노출수", format: num },
  clk: { label: "클릭수", format: num },
  conv_cnt: { label: "전환수", format: num },
  conv_amt: { label: "전환매출", format: won },
  cpc: { label: "CPC", format: won },
  cpa: { label: "CPA", format: won },
  roas_naver: { label: "ROAS", format: roasX },
};
const METRIC_KEYS: ChartMetricKey[] = ["cost", "imp", "clk", "conv_cnt", "conv_amt", "cpc", "cpa", "roas_naver"];

interface ChartBucketRow {
  label: string;
  cost: number;
  imp: number;
  clk: number;
  conv_cnt: number;
  conv_amt: number;
  cpc: number | null;
  cpa: number | null;
  roas_naver: number | null;
}

// 기존 리포트 API의 일별 trend를 프론트에서 N일 단위로 묶어 재집계(백엔드 미변경 — 계획서 §1 T3 원칙).
// 파생 지표(cpc/cpa/roas_naver)는 버킷 합산 후 계산 — 일별 값의 평균이 아님.
function bucketTrend(trend: NaverAdTrendRow[], bucketDays: number): ChartBucketRow[] {
  const sorted = [...trend].sort((a, b) => (a.ad_date < b.ad_date ? -1 : a.ad_date > b.ad_date ? 1 : 0));
  const out: ChartBucketRow[] = [];
  for (let i = 0; i < sorted.length; i += bucketDays) {
    const chunk = sorted.slice(i, i + bucketDays);
    const sum = chunk.reduce(
      (acc, r) => {
        acc.imp += r.imp;
        acc.clk += r.clk;
        acc.cost += r.cost;
        acc.conv_cnt += r.conv_cnt;
        acc.conv_amt += r.conv_amt;
        return acc;
      },
      { imp: 0, clk: 0, cost: 0, conv_cnt: 0, conv_amt: 0 },
    );
    const label =
      chunk.length > 1
        ? `${shortDate(chunk[0].ad_date)}~${shortDate(chunk[chunk.length - 1].ad_date)}`
        : shortDate(chunk[0].ad_date);
    out.push({
      label,
      imp: sum.imp,
      clk: sum.clk,
      cost: sum.cost,
      conv_cnt: sum.conv_cnt,
      conv_amt: sum.conv_amt,
      cpc: sum.clk ? sum.cost / sum.clk : null,
      cpa: sum.conv_cnt ? sum.cost / sum.conv_cnt : null,
      roas_naver: sum.cost ? sum.conv_amt / sum.cost : null,
    });
  }
  return out;
}

function MopKpiCard({
  label,
  value,
  delta,
  invert,
}: {
  label: string;
  value: string;
  delta?: number | null;
  invert?: boolean;
}) {
  const d = delta === undefined ? null : mopDelta(delta, invert);
  return (
    <div className="bg-white rounded border border-gray-200 p-3">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="text-lg font-semibold text-gray-900 tabular-nums">{value}</div>
      {d && <div className={`text-xs mt-0.5 font-medium ${d.cls}`}>{d.text}</div>}
    </div>
  );
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
  const [chartMetricA, setChartMetricA] = useState<ChartMetricKey>("clk");
  const [chartMetricB, setChartMetricB] = useState<ChartMetricKey>("imp");
  const [chartBucket, setChartBucket] = useState<1 | 7 | 14 | 30>(1);

  const [report, setReport] = useState<NaverAdReportData | null>(null);
  const [bep, setBep] = useState<NaverAdBepList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqSeq = useRef(0);

  async function load() {
    const mySeq = ++reqSeq.current;
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
      if (mySeq !== reqSeq.current) return; // 응답이 늦게 도착한 stale 요청 — 최신 상태만 반영(레이스 방지)
      setReport(data);
    } catch (e: any) {
      if (mySeq !== reqSeq.current) return;
      setError(e.message);
    } finally {
      if (mySeq === reqSeq.current) setLoading(false);
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

  // ── T3 KPI 스트립: CPA는 백엔드 미제공 파생값 → 기간 합산 cost/conv_cnt로 프론트 계산.
  // CPC·CPA 델타도 백엔드 deltas_pct에 없어 동일 공식(localDeltaPct)으로 프론트 계산.
  const kpiCpa = deriveCpa(kpis);
  const cmpCpa = deriveCpa(compare?.kpis);
  const cpcDelta = compareOn ? localDeltaPct(kpis?.cpc, compare?.kpis?.cpc) : undefined;
  const cpaDelta = compareOn ? localDeltaPct(kpiCpa, cmpCpa) : undefined;

  const chartRows = report ? bucketTrend(report.trend, chartBucket) : [];

  function onToggleCompare(checked: boolean) {
    setCompareOn(checked);
    // "켜면 직전 동일 길이 기간과 비교"(계획서 §1 T3) — 처음 켤 때만 자동 채움, 이후 수동 수정은 유지.
    if (checked && !compareFrom && !compareTo) {
      const prev = prevPeriod(dateFrom, dateTo);
      setCompareFrom(prev.from);
      setCompareTo(prev.to);
    }
  }

  function onSelectMetricA(v: ChartMetricKey) {
    if (v === chartMetricB) setChartMetricB(chartMetricA);
    setChartMetricA(v);
  }

  function onSelectMetricB(v: ChartMetricKey) {
    if (v === chartMetricA) setChartMetricA(chartMetricB);
    setChartMetricB(v);
  }

  return (
    <div className="space-y-6">
      <LayerNav />
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">네이버 SA 광고 리포트</h1>
        <p className="text-xs text-gray-400">D-NAO-15: 읽기 전용 리포트 코어 (제안·쓰기 없음)</p>
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
            <input type="checkbox" checked={compareOn} onChange={(e) => onToggleCompare(e.target.checked)} />
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

      {/* KPI 스트립 8칸 (MOP `15_report_campaign.png` 상단 패턴, D-NAO-15+T3) — 조회기간 합산,
          파생지표(CPC/CPA/ROAS)는 합산 후 계산. compareOn일 때만 증감% 병기(▲빨강/▼파랑,
          비용성 지표는 색 반전 — mopDelta 참조). CTR·평균순위는 기존 드릴다운 표에 존속. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MopKpiCard label="광고비" value={won(kpis?.cost)} delta={compareOn ? compare?.deltas_pct?.cost : undefined} invert />
        <MopKpiCard label="노출수" value={num(kpis?.imp)} delta={compareOn ? compare?.deltas_pct?.imp : undefined} />
        <MopKpiCard label="클릭수" value={num(kpis?.clk)} delta={compareOn ? compare?.deltas_pct?.clk : undefined} />
        <MopKpiCard label="전환수" value={num(kpis?.conv_cnt)} delta={compareOn ? compare?.deltas_pct?.conv_cnt : undefined} />
        <MopKpiCard label="전환매출" value={won(kpis?.conv_amt)} delta={compareOn ? compare?.deltas_pct?.conv_amt : undefined} />
        <MopKpiCard label="CPC" value={won(kpis?.cpc)} delta={cpcDelta} invert />
        <MopKpiCard label="CPA" value={won(kpiCpa)} delta={cpaDelta} invert />
        <MopKpiCard label="ROAS" value={roasX(kpis?.roas_naver)} delta={compareOn ? compare?.deltas_pct?.roas_naver : undefined} />
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

      {/* 이중축 라인차트 (MOP `15b` 패턴): 지표 2개 선택 + 기간단위 토글, 좌축=파랑/우축=빨강,
          축 눈금 숫자도 라인과 동색(MOP 시그니처). 기존 리포트 API의 일별 trend를 프론트에서
          버킷 재집계(bucketTrend) — 백엔드 미변경. */}
      <div className="bg-white border rounded gap-3 p-4">
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <h3 className="text-sm font-medium text-gray-700">기간별 추이</h3>
          <div className="flex items-center gap-1.5 text-sm">
            <label className="text-gray-500 text-xs">지표1(좌축)</label>
            <select value={chartMetricA} onChange={(e) => onSelectMetricA(e.target.value as ChartMetricKey)}
              className="text-sm border border-gray-300 rounded px-1.5 py-1">
              {METRIC_KEYS.map((k) => <option key={k} value={k}>{METRIC_META[k].label}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-1.5 text-sm">
            <label className="text-gray-500 text-xs">지표2(우축)</label>
            <select value={chartMetricB} onChange={(e) => onSelectMetricB(e.target.value as ChartMetricKey)}
              className="text-sm border border-gray-300 rounded px-1.5 py-1">
              {METRIC_KEYS.map((k) => <option key={k} value={k}>{METRIC_META[k].label}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-1 ml-auto">
            {([1, 7, 14, 30] as const).map((d) => (
              <button key={d} onClick={() => setChartBucket(d)}
                className={`px-2 py-1 text-xs rounded border ${chartBucket === d ? "bg-blue-600 text-white border-blue-600" : "text-gray-600 border-gray-300 hover:bg-gray-50"}`}>
                {d}일
              </button>
            ))}
          </div>
        </div>
        {loading && !report ? (
          <div className="h-72 bg-gray-50 rounded animate-pulse" />
        ) : !report || chartRows.length === 0 ? (
          <div className="h-72 flex items-center justify-center text-gray-400 text-sm">데이터가 없습니다</div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={chartRows}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="label" tick={{ fontSize: 12, fill: "#9ca3af" }} />
              <YAxis yAxisId="left" tick={{ fontSize: 12, fill: "#2563eb" }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 12, fill: "#dc2626" }} />
              <Tooltip formatter={(v, name) => {
                const key = name === METRIC_META[chartMetricA].label ? chartMetricA : chartMetricB;
                return METRIC_META[key].format(Number(v));
              }} />
              <Legend />
              <Line yAxisId="left" type="monotone" dataKey={chartMetricA} name={METRIC_META[chartMetricA].label}
                stroke="#2563eb" strokeWidth={2} dot={{ r: 3 }} connectNulls />
              <Line yAxisId="right" type="monotone" dataKey={chartMetricB} name={METRIC_META[chartMetricB].label}
                stroke="#dc2626" strokeWidth={2} dot={{ r: 3 }} connectNulls />
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
                {report.hourly_meta?.ad_date && (
                  <tr>
                    <th colSpan={4} className="px-4 py-2 text-xs font-normal text-gray-400 text-left bg-amber-50 border-b border-amber-100">
                      {report.hourly_meta.ad_date} 하루치 표시 — 조회 기간과 무관하게 최신(또는 종료일 이하 최신) 하루만 보여줍니다
                      {report.hourly_meta.clamped > 0 && (
                        <span className="ml-2 text-amber-600">· 데이터 이상 {report.hourly_meta.clamped}건 감지(누적 감소 보정됨)</span>
                      )}
                    </th>
                  </tr>
                )}
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
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.imp)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.clk)}</td>
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
                        : r.keyword_id ? `${r.adgroup_id} / ${r.keyword_id}` : `${r.adgroup_id} (키워드 없음 — 쇼핑검색 등)`}
                    </td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.imp)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.clk)}</td>
                    <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{pctFromFraction(r.ctr)}</td>
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
