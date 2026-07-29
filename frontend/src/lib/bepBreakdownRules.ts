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

// ── ⑤ "N위 시장가" 캡션 — market_bid_device/observed_on 표기(Phase 3 후속) ──
// 시장가는 "최근 관측일의 최댓값"이라 최대 7일 전 값일 수 있다. 기기·관측일 없이 숫자만
// 보여주면 신선도가 없는데 있는 척하는 것이다(원칙22) — 그래서 셀 아래 작은 글씨로 함께 낸다.

/** device 코드 → 화면 표기. 알려진 두 값 외(빈 값 포함)는 빈 문자열 — 배지를 만들지 않는다. */
export function marketBidDeviceLabel(device: string | null | undefined): string {
  if (device === "MOBILE") return "모바일";
  if (device === "PC") return "PC";
  return "";
}

/** 'YYYY-MM-DD' → 'MM-DD'. 형식이 다르면(추정 금지) 원문을 그대로 돌려준다 — 억지로 잘라
 *  틀린 날짜를 만들지 않는다. */
export function shortObservedDate(iso: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  return `${match[1]}-${match[2]}`;
}

/** ⑤ 표 "N위 시장가" 셀 아래 붙일 캡션. 관측일이 없으면(market_bid가 null인 행) 빈 문자열 —
 *  호출부는 이 값이 ""면 아무것도 렌더하지 않는다(NO_DATA만 남는다). 기기를 모르면 관측일만
 *  보여준다("07-28 관측"). */
export function marketBidCaption(
  device: string | null | undefined,
  observedOn: string | null | undefined,
): string {
  if (!observedOn) return "";
  const dev = marketBidDeviceLabel(device);
  const when = `${shortObservedDate(observedOn)} 관측`;
  return dev ? `${dev} · ${when}` : when;
}
