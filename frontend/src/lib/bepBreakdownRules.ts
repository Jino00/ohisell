// bepBreakdownRules.ts — ⑤BEP 구성 섹션의 순수 로직(성과뷰 Phase 3, D-NAO-104).
//
// ★렌더 테스트 관례가 없는 프로젝트라(@testing-library/react 미설치), 판정/가공 로직을
//   컴포넌트 밖으로 빼서 순수 함수로 테스트한다(기존 perfDateRules.ts와 같은 패턴).
//
// 이 파일에 담는 두 가지:
//  1. stripBoldMarkers — 백엔드 문장(ceiling_basis/blocked_reason/sentence)에 마크다운 굵게
//     `**…**`가 섞여 올 수 있다(bep_breakdown.py `_pick_ceiling` 참고). dangerouslySetInnerHTML은
//     쓰지 않기로 했으므로(XSS 표면 확대 금지), `**`만 제거해 평문으로 보여준다 — 강조 표시
//     자체를 재현하지 않는다(이 화면엔 그 정도 강조가 필요 없다는 판단).
//  2. marketBidTone — "지금 시장가"가 "클릭당 상한"보다 비싸면 손해다. 그 판정을 한 곳에
//     모아 컴포넌트에서 중복 if/else가 생기지 않게 한다. null은 항상 "idle"(모름 ≠ 나쁨,
//     원칙22) — 회색으로 렌더한다.

/** `**텍스트**` → `텍스트`. 마크다운 전체를 해석하지 않고 굵게 표시 문법만 제거한다. */
export function stripBoldMarkers(s: string): string {
  return s.replace(/\*\*(.*?)\*\*/g, "$1");
}

export type MarketBidTone = "good" | "bad" | "idle";

/** 시장가(4위 관측)가 상한보다 비싸면 "bad"(손해), 상한 이내면 "good", 둘 중 하나라도
 *  모르면 "idle". 같으면(=상한과 정확히 일치) "손해는 아니다" 쪽으로 good.
 *
 *  ceilingIsBorrowed=true면 good/bad 판정을 하지 않고 무조건 "idle"이다 — 이 행의 상한은
 *  이 상품 자체 표본이 아니라 계정 평균을 빌려 계산된 값이라 실제보다 후하게(낙관적으로)
 *  나왔을 수 있다. 그런 값으로 초록/빨강을 매기면 근거 없는 확신을 색으로 주는 것이다. */
export function marketBidTone(
  marketBid: number | null | undefined,
  ceilingBid: number | null | undefined,
  ceilingIsBorrowed = false,
): MarketBidTone {
  if (marketBid == null || ceilingBid == null) return "idle";
  if (ceilingIsBorrowed) return "idle";
  return marketBid > ceilingBid ? "bad" : "good";
}
