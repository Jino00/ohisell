// PerfCampaignTrendChart.tsx — 광고 성과(사장님 뷰) ③ 일별 ROAS 추이 + 기준선
// (D-NAO-105 Phase 2, 계획서 §5). recharts 사용 패턴은 NaverAdReport.tsx를 그대로 따른다.
//
// ★이 차트가 답하는 질문: "이 광고, 남는 선 위에서 돌고 있나?" — 그래서 선 하나(ROAS)와
//   기준선 둘(손익분기·목표)만 그린다. 지표를 더 얹으면 그 질문이 안 보인다.
// ★D-0(오늘)은 애초에 series에 없다(백엔드 창 관례) — 확정치와 프록시를 같은 선에 그리면
//   마지막 점만 정의가 다른 선이 된다.
// ★null(광고비 0인 날)은 **점을 찍지 않는다**. connectNulls를 켜서 선을 이어버리면 "그날도
//   이 정도였다"는 없는 사실을 그리게 된다(원칙22).
// ★Phase 3 후속: events(개선 이벤트 마커) — 인과 주장 없음. "이 날 결정이 있었다"는 표식뿐,
//   그 결정이 뒤 구간의 변화를 "설명한다"는 판단은 이 차트도, EventMarker도 하지 않는다
//   (해석 문장은 ⑥ 타임라인 카드의 백엔드 sentence 몫).
import {
  CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import type { NaverPerformanceSeriesPoint } from "../lib/api";
import { NO_DATA, roasX, won } from "../lib/format";
import { filterMarkersInRange, type ChartEventMarker } from "../lib/timelineRules";
import { EventMarker } from "./ui/EventMarker";

const COLOR_ROAS = "#2563eb";
const COLOR_COST = "#9ca3af";
const COLOR_BEP = "#dc2626";     // 남는 기준 — 아래로 내려가면 손해
const COLOR_TARGET = "#16a34a";  // 목표

/** 'YYYY-MM-DD' → 'M/D' (축 라벨. 30개가 겹치지 않아야 한다). */
function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}

export function PerfCampaignTrendChart({
  series, bepRoas, targetRoas, events = [],
}: {
  series: NaverPerformanceSeriesPoint[];
  bepRoas: number | null;
  targetRoas: number | null;
  /** 개선 이벤트 마커(없으면 아무것도 그리지 않는다). 시리즈 날짜 범위 밖은 이 컴포넌트가
   *  자체적으로 걸러낸다(filterMarkersInRange) — 호출부가 이미 걸렀어도 다시 확인한다. */
  events?: ChartEventMarker[];
}) {
  if (series.length === 0) {
    return (
      <p className="p-4 text-sm text-gray-500">
        이 기간에는 집행된 광고가 없어 추이를 그릴 수 없습니다.
      </p>
    );
  }
  const rows = series.map((p) => ({ ...p, label: shortDate(p.date) }));
  const visibleEvents = filterMarkersInRange(events, series.map((p) => p.date));

  return (
    <div>
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#9ca3af" }} minTickGap={16} />
          <YAxis yAxisId="roas" tick={{ fontSize: 11, fill: COLOR_ROAS }}
                 tickFormatter={(v) => `${v}배`} />
          <YAxis yAxisId="cost" orientation="right" tick={{ fontSize: 11, fill: COLOR_COST }}
                 tickFormatter={(v) => `${Math.round(Number(v) / 10000)}만`} />
          <Tooltip
            formatter={(value, name) =>
              name === "광고비"
                ? won(Number(value))
                : value == null ? NO_DATA : roasX(Number(value))
            }
            labelFormatter={(l) => `${l}`}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {/* 기준선 — 이 두 줄이 "남는가"의 전부다. */}
          {bepRoas != null && (
            <ReferenceLine yAxisId="roas" y={bepRoas} stroke={COLOR_BEP} strokeDasharray="4 3"
              label={{ value: `남는 기준 ${bepRoas.toFixed(2)}배`, position: "insideTopLeft",
                       fontSize: 11, fill: COLOR_BEP }} />
          )}
          {targetRoas != null && (
            <ReferenceLine yAxisId="roas" y={targetRoas} stroke={COLOR_TARGET} strokeDasharray="4 3"
              label={{ value: `목표 ${targetRoas.toFixed(2)}배`, position: "insideBottomLeft",
                       fontSize: 11, fill: COLOR_TARGET }} />
          )}
          {/* 개선 이벤트 마커 — x는 XAxis의 dataKey("label", shortDate 형식)와 맞춰야 한다.
              yAxisId를 명시하지 않으면 recharts 기본값 0으로 떨어지는데, 이 차트엔 id=0인
              YAxis가 없어(둘 다 "roas"/"cost") 그 경우 세로 스팬을 못 구해 렌더되지 않는다. */}
          {visibleEvents.map((ev) => (
            <EventMarker key={ev.date} date={shortDate(ev.date)} label={ev.label}
              count={ev.count} yAxisId="roas" />
          ))}
          <Line yAxisId="cost" type="monotone" dataKey="cost" name="광고비"
                stroke={COLOR_COST} strokeWidth={1} dot={false} />
          {/* connectNulls 없음 — 값이 없는 날은 선을 끊는다(없는 사실을 그리지 않는다). */}
          <Line yAxisId="roas" type="monotone" dataKey="roas" name="ROAS"
                stroke={COLOR_ROAS} strokeWidth={2} dot={{ r: 2 }} />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="mt-1 text-xs text-gray-400">
        빨간 점선 아래로 내려간 날은 손해입니다. 초록 점선 위는 목표를 넘긴 날입니다.
      </p>
    </div>
  );
}
