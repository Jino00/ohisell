// PerfBudgetCurveChart.tsx — 광고 성과(사장님 뷰) ④ 시간별 예산 소진 곡선
// (D-NAO-105 Phase 2, 계획서 §5·§4-ⓒ).
//
// ★이 차트가 답하는 질문: "오늘 예산 다 썼나 / 언제 멈췄나?" — 누적 소진선 + 일예산 기준선 +
//   **멈춘 구간 음영**이 전부다.
// ★일예산이 없으면 기준선도 음영도 그리지 않는다 — 0원 기준선을 그리면 항상 초과로 보인다.
// ★예산 증액(BP 레인) 마커는 그 시각에 세로선으로 표시한다. 증액 기록이 없으면 마커도 없다
//   (없는 것을 없다고 말하는 자리는 차트가 아니라 아래 문장이다).
import {
  Area, CartesianGrid, ComposedChart, Legend, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { NaverPerformanceBudgetCurve } from "../lib/api";
import { won } from "../lib/format";

const COLOR_SPEND = "#2563eb";
const COLOR_BUDGET = "#dc2626";
const COLOR_BLACKOUT = "#94a3b8";
const COLOR_CHANGE = "#7c3aed";

/** 연속된 시각들을 [시작, 끝] 구간으로 묶는다. [14,15,16,20] → [[14,16],[20,20]].
 *  ★한 시간마다 ReferenceArea를 하나씩 그리면 경계선이 격자처럼 보여 '멈춘 구간'이 안 읽힌다. */
export function toRanges(hours: number[]): [number, number][] {
  const sorted = [...hours].sort((a, b) => a - b);
  const out: [number, number][] = [];
  for (const h of sorted) {
    const last = out[out.length - 1];
    if (last && h === last[1] + 1) last[1] = h;
    else out.push([h, h]);
  }
  return out;
}

export function PerfBudgetCurveChart({ curve, changeHours = [] }: {
  curve: NaverPerformanceBudgetCurve;
  /** 이 캠페인의 예산이 바뀐 시각(BP 자동 증액 포함). 세로 마커로 표시한다. */
  changeHours?: number[];
}) {
  if (curve.points.length === 0) {
    return <p className="p-4 text-sm text-gray-500">이날은 시간별 관측 기록이 없습니다.</p>;
  }
  const budget = curve.daily_budget;
  const ranges = budget == null ? [] : toRanges(curve.blackout_hours);
  const rows = curve.points.map((p) => ({ ...p, label: `${p.hour}시` }));
  const observedHours = new Set(curve.points.map((p) => p.hour));
  // 관측이 없는 시각에 마커를 그리면 X축 카테고리에 없어서 조용히 사라진다 — 있는 것만 그린다.
  const markers = [...new Set(changeHours)].filter((h) => observedHours.has(h));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#9ca3af" }} minTickGap={12} />
        <YAxis tick={{ fontSize: 11, fill: "#9ca3af" }}
               tickFormatter={(v) => `${Math.round(Number(v) / 10000)}만`} />
        <Tooltip formatter={(v, name) => [won(Number(v)), String(name)]} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {/* 예산 도달로 멈춘 구간 — 음영. */}
        {ranges.map(([from, to]) => (
          <ReferenceArea key={`bo-${from}`} x1={`${from}시`} x2={`${to}시`}
            fill={COLOR_BLACKOUT} fillOpacity={0.18} />
        ))}
        {budget != null && (
          <ReferenceLine y={budget} stroke={COLOR_BUDGET} strokeDasharray="4 3"
            label={{ value: `하루 예산 ${Math.round(budget / 10000)}만원`,
                     position: "insideTopRight", fontSize: 11, fill: COLOR_BUDGET }} />
        )}
        {/* 예산을 바꾼 시각 — 곡선이 꺾이는 이유가 여기 있다. */}
        {markers.map((h) => (
          <ReferenceLine key={`chg-${h}`} x={`${h}시`} stroke={COLOR_CHANGE} strokeDasharray="2 2"
            label={{ value: "예산 변경", position: "top", fontSize: 10, fill: COLOR_CHANGE }} />
        ))}
        <Area type="monotone" dataKey="cost" name="누적 광고비"
              stroke={COLOR_SPEND} fill={COLOR_SPEND} fillOpacity={0.12} strokeWidth={2} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
