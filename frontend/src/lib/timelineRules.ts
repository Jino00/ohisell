// timelineRules.ts — ⑥개선 타임라인 섹션의 순수 로직(성과뷰 Phase 3, D-NAO-104).
//
// ★렌더 테스트 관례가 없는 프로젝트라(@testing-library/react 미설치), 판정/가공 로직을
//   컴포넌트 밖으로 빼서 순수 함수로 테스트한다(bepBreakdownRules.ts와 같은 패턴).
//
// ★이 섹션의 정직 규약(계획서 §3-3 · 원칙22)은 백엔드 문장을 그대로 renders하는 것이지,
//   여기서 새 해석 문구("개선됐습니다" 류)를 만들지 않는 것이다. 이 파일이 만드는 문구는
//   전부 "몇 번째 날짜인지/무슨 뜻인지"를 알려주는 라벨일 뿐, 판단/단정 문구가 아니다.
//   회귀 테스트(timelineRules.test.ts)가 "개선/덕분/효과" 금지어를 지킨다.

import type {
  NaverPerformanceTimelineDay, NaverPerformanceTimelineImpact, NaverPerformanceTimelineWindow,
} from "./api";

/** effective_confidence → 화면에 붙일 꼬리표.
 *  · "commit" — 그 결정을 인용한 첫 커밋 날짜이지 실제 배포/적용 시각이 아니다. 무표기로
 *    두면 추정을 확정처럼 보여주므로 "코드 반영일 기준" 꼬리표를 단다.
 *  · "log"는 실제 기록 시각이므로 꼬리표 없음(""). */
export function confidenceLabel(confidence: string): string {
  if (confidence === "commit") return "코드 반영일 기준";
  if (confidence === "assumed") return "적용 시점 추정";
  if (confidence === "unknown") return "시점 불명";
  return "";
}

/** impact.post가 아직 7일을 못 채웠으면 "관찰 중 (N/7일)" 배지 문자열. 다 채웠거나
 *  impact 자체가 없으면(날짜 파싱 실패) null — 배지를 그리지 않는다. */
export function observationBadge(
  impact: NaverPerformanceTimelineImpact | null,
): string | null {
  if (impact == null) return null;
  if (impact.post.complete) return null;
  return `관찰 중 (${impact.post.days}/7일)`;
}

/** pre→post 숫자 한 쌍을 "A → B" 문자열로. null은 호출부가 NO_DATA로 이미 바꿔서 넘긴다 —
 *  이 함수는 포맷된 문자열 두 개를 이어붙이는 것만 한다(포맷 자체는 lib/format.ts 몫). */
export function deltaText(preFormatted: string, postFormatted: string): string {
  return `${preFormatted} → ${postFormatted}`;
}

/** 전·후 창 중 하나라도 days_with_data < days(달력상 일수보다 실제 적재일이 적음)이면
 *  "기록이 있는 날만 셌습니다" 보조 문구. 둘 다 온전하면 null — 화면에 불필요한 문구를
 *  얹지 않는다. 숫자 줄 옆에 작은 회색 글씨로 보여줄 용도(백엔드 sentence에도 같은 사실이
 *  들어가지만, 문장 속에 묻히지 않도록 별도로 노출한다). */
export function partialDataNote(
  pre: NaverPerformanceTimelineWindow,
  post: NaverPerformanceTimelineWindow,
): string | null {
  if (pre.days_with_data >= pre.days && post.days_with_data >= post.days) return null;
  return `기록이 있는 날만 셌습니다 (전 ${pre.days_with_data}일 · 후 ${post.days_with_data}일)`;
}

// ── ③차트 마커 — timeline 응답을 PerfCampaignTrendChart의 events prop으로 (Phase 3 후속) ──
// ★인과 주장 없음(이 파일 상단 규약 그대로) — 여기서 만드는 건 "그날 결정이 있었다"는 표식과
//   개수뿐이다. "그래서 좋아졌다" 류 해석은 이 함수들의 책임이 아니다.

export interface ChartEventMarker {
  date: string;
  /** 그날 첫 이벤트의 label_ko — 여러 건이어도 대표로 하나만. 개수는 count로 따로 낸다. */
  label: string;
  count: number;
}

/** 이미 만든 마커 목록 중 seriesDates(차트에 실제로 그려지는 날짜들)의 첫~마지막 범위 밖인
 *  것만 제거한다. seriesDates가 비어있으면(집행 이력 없음) 그릴 축 자체가 없으므로 빈 배열.
 *  ★차트 컴포넌트가 호출부와 무관하게 스스로도 거는 방어적 필터 — 범위 밖 마커가 하나라도
 *  섞이면 차트 x축이 그 마커 때문에 늘어난다(계획서 §5 요구사항). */
export function filterMarkersInRange(
  markers: ChartEventMarker[],
  seriesDates: string[],
): ChartEventMarker[] {
  if (seriesDates.length === 0) return [];
  const first = seriesDates[0];
  const last = seriesDates[seriesDates.length - 1];
  return markers.filter((m) => m.date >= first && m.date <= last);
}

/** timeline 응답(날짜별 이벤트 묶음)을 차트 마커로 변환하고, 곧바로 seriesDates 범위로
 *  걸러낸다. 이벤트가 없는 날은 마커를 만들지 않는다. */
export function toChartMarkers(
  timeline: NaverPerformanceTimelineDay[],
  seriesDates: string[],
): ChartEventMarker[] {
  const markers = timeline
    .filter((day) => day.events.length > 0)
    .map((day) => ({ date: day.date, label: day.events[0].label_ko, count: day.events.length }));
  return filterMarkersInRange(markers, seriesDates);
}

/** 차트 마커 라벨 — 12자를 넘으면 자른다(차트가 라벨로 덮이면 못 읽는다). count>1이면
 *  "{label} 외 {count-1}건"이 아니라 "{label} +{count-1}"로(공간 절약). */
export function formatEventMarkerLabel(label: string, count: number): string {
  const short = label.length > 12 ? label.slice(0, 12) : label;
  return count > 1 ? `${short} +${count - 1}` : short;
}
