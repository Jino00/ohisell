// timelineRules.ts — ⑥개선 타임라인 섹션의 순수 로직(성과뷰 Phase 3, D-NAO-104).
//
// ★렌더 테스트 관례가 없는 프로젝트라(@testing-library/react 미설치), 판정/가공 로직을
//   컴포넌트 밖으로 빼서 순수 함수로 테스트한다(bepBreakdownRules.ts와 같은 패턴).
//
// ★이 섹션의 정직 규약(계획서 §3-3 · 원칙22)은 백엔드 문장을 그대로 renders하는 것이지,
//   여기서 새 해석 문구("개선됐습니다" 류)를 만들지 않는 것이다. 이 파일이 만드는 문구는
//   전부 "몇 번째 날짜인지/무슨 뜻인지"를 알려주는 라벨일 뿐, 판단/단정 문구가 아니다.
//   회귀 테스트(timelineRules.test.ts)가 "개선/덕분/효과" 금지어를 지킨다.

import type { NaverPerformanceTimelineImpact, NaverPerformanceTimelineWindow } from "./api";

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
