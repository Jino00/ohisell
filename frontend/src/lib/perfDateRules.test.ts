// perfDateRules.test.ts — 광고 성과 뷰 날짜 선택·비교 규칙 가드(D-NAO-105 Phase 2).
//
// ★존재 이유: `<input type="date">`는 사용자가 값을 지우면 **빈 문자열**을 준다. 그대로
//   `fetchNaverPerformanceDay({date: date || undefined})`로 넘기면 날짜 파라미터가 통째로
//   빠지고 백엔드는 그것을 '오늘'로 해석한다 — 사용자는 날짜를 지웠는데 화면엔 오늘 숫자가
//   그대로 남는다. 에러도 안 나고 화면도 안 비어서 **아무도 모르는 거짓말**이 된다.
//   (같은 클래스의 함정을 커맨드 센터에서 한 번 겪었다 — changeLogPeriod.test.ts 참조.)
import { describe, it, expect } from "vitest";
import { baseDateError, compareDateError, defaultCompareDate } from "./perfDateRules";

const TODAY = "2026-07-28";

describe("baseDateError — 기준 날짜", () => {
  it("오늘은 통과한다", () => {
    expect(baseDateError(TODAY, TODAY)).toBeNull();
  });

  it("과거는 통과한다", () => {
    expect(baseDateError("2026-07-01", TODAY)).toBeNull();
  });

  it("★날짜를 지우면 막는다 (회귀 고정: 빈 값은 '오늘'로 조용히 해석된다)", () => {
    expect(baseDateError("", TODAY)).toBe("날짜를 선택하세요.");
  });

  it("미래는 막는다 — 백엔드도 400이지만 원문을 화면에 띄우지 않는다", () => {
    expect(baseDateError("2026-07-29", TODAY)).toBe("아직 오지 않은 날짜입니다.");
  });
});

describe("compareDateError — 비교 날짜", () => {
  it("서로 다른 두 날은 통과한다", () => {
    expect(compareDateError(TODAY, "2026-07-21", TODAY)).toBeNull();
  });

  it("비교 날짜를 지우면 막는다", () => {
    expect(compareDateError(TODAY, "", TODAY)).toBe("비교할 날짜를 선택하세요.");
  });

  it("같은 날끼리 비교는 막는다 (백엔드 400을 화면에서 먼저 말한다)", () => {
    expect(compareDateError(TODAY, TODAY, TODAY)).toBe("기준 날짜와 비교 날짜가 같습니다.");
  });

  it("미래 비교 날짜는 막는다", () => {
    expect(compareDateError(TODAY, "2026-08-01", TODAY))
      .toBe("비교 날짜가 아직 오지 않은 날짜입니다.");
  });
});

describe("defaultCompareDate — 기본 비교일", () => {
  it("7일 전 = 같은 요일(주중/주말 편차를 타지 않는다)", () => {
    expect(defaultCompareDate("2026-07-28")).toBe("2026-07-21");
  });

  it("월 경계를 넘어간다", () => {
    expect(defaultCompareDate("2026-08-03")).toBe("2026-07-27");
  });

  it("연 경계를 넘어간다", () => {
    expect(defaultCompareDate("2026-01-03")).toBe("2025-12-27");
  });
});
