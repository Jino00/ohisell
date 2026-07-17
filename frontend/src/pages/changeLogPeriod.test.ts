// changeLogPeriod.test.ts — 변경 이력 커스텀 기간 검증 가드(D-NAO-54).
// ★존재 이유: `<input type="date">`는 사용자가 값을 지우면 **빈 문자열**을 준다. 최초 구현은
//   `from > to` 하나만 봤는데 `"" > "2026-07-17"`는 false라 그 검사를 그냥 통과했다(실측).
//   그러면 `date_from=`이 그대로 백엔드로 나가고 422 + pydantic 원문이 화면에 뜬다
//   ("불러오지 못했습니다: Input should be a valid date…") — 사용자에게 아무 의미 없는 문자열이다.
//   ISO 날짜의 문자열 비교가 곧 날짜 비교인 건 **양쪽이 채워졌을 때만** 참이다.
import { describe, it, expect } from "vitest";
import { customRangeError } from "./NaverAdCommandCenter";

describe("customRangeError — 캘린더 커스텀 구간 검증", () => {
  it("정상 구간은 null (조회 가능)", () => {
    expect(customRangeError({ from: "2026-07-10", to: "2026-07-17" })).toBeNull();
  });

  it("같은 날 하루짜리 구간도 정상 (당일/어제 프리셋과 같은 모양)", () => {
    expect(customRangeError({ from: "2026-07-17", to: "2026-07-17" })).toBeNull();
  });

  it("뒤집힌 구간은 막는다 — 캘린더에서 실제로 고를 수 있다", () => {
    expect(customRangeError({ from: "2026-07-17", to: "2026-07-10" })).toBe("시작일이 종료일보다 늦습니다.");
  });

  it("★시작일을 지우면 막는다 (회귀 고정: 빈 문자열은 from>to를 통과한다)", () => {
    // 이 한 줄이 버그의 정체다 — 방어가 없으면 아래가 false라 요청이 나간다.
    expect("" > "2026-07-17").toBe(false);
    expect(customRangeError({ from: "", to: "2026-07-17" })).toBe("시작일과 종료일을 모두 선택하세요.");
  });

  it("★종료일을 지워도 막는다", () => {
    expect(customRangeError({ from: "2026-07-17", to: "" })).toBe("시작일과 종료일을 모두 선택하세요.");
  });

  it("둘 다 지우면 막는다", () => {
    expect(customRangeError({ from: "", to: "" })).toBe("시작일과 종료일을 모두 선택하세요.");
  });
});
