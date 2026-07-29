// EventMarker.tsx — ③캠페인 추이 차트용 개선 이벤트 마커(성과뷰 Phase 3 후속, 계획서 §5).
//
// ★recharts 3.8.1에서 <ReferenceLine>은 정적 자식 스캔이 아니라 마운트 시 내부 상태(store)에
//   자가등록하는 방식으로 렌더된다(node_modules/recharts/es6/cartesian/ReferenceLine.js —
//   `ReportReferenceLine`이 useEffect에서 `dispatch(addLine(props))`, 실제 그리기는
//   `ReferenceLineImpl`이 그 상태를 셀렉터로 읽어 처리). 즉 <ComposedChart>의 "직계" 자식일
//   필요가 없다 — 그 서브트리 안 어디에서 렌더되든(이 컴포넌트로 한 번 감싸도) 정상 표시된다.
//   구 recharts 2.x의 "직계 자식만 인식"(React.Children 정적 스캔) 가정은 이 버전엔 적용되지
//   않는다(소스로 확인 — 라이브 브라우저 QA 없이도 이 사실은 정적 검증 가능하다).
import { ReferenceLine } from "recharts";
import { formatEventMarkerLabel } from "../../lib/timelineRules";

export const COLOR_EVENT = "#9333ea"; // 개선 이벤트 마커 — BEP(red)/target(green)과 구분되는 보라

export function EventMarker({
  date, label, count, xAxisId, yAxisId,
}: {
  /** ReferenceLine의 x — 차트 XAxis의 dataKey 값(카테고리)과 정확히 같은 문자열이어야 한다.
   *  호출부(PerfCampaignTrendChart)가 시리즈의 축 라벨 형식으로 변환해서 넘긴다. */
  date: string;
  label: string;
  count: number;
  xAxisId?: string;
  yAxisId?: string;
}) {
  return (
    <ReferenceLine
      x={date}
      xAxisId={xAxisId}
      yAxisId={yAxisId}
      stroke={COLOR_EVENT}
      strokeDasharray="2 2"
      label={{
        value: formatEventMarkerLabel(label, count),
        position: "top",
        fontSize: 10,
        fill: COLOR_EVENT,
      }}
    />
  );
}
