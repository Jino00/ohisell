// format.test.ts — D-NAO-47 P2-T2. 프론트 첫 테스트 파일.
// ★존재 이유: pct()가 서로 호환 안 되는 입력 계약 2종(분수 0~1 / 스케일 0~100)으로
//   중복 정의돼 있었다. 순진하게 합치면 5%가 0.05%로 조용히 렌더된다(타입 에러도 안 남).
//   pctFromFraction은 이름으로 계약을 선언하고, 이 테스트가 그 계약을 고정한다.
import { describe, it, expect } from "vitest";
import { isoKST, num, won, pctFromFraction, roasX, NO_DATA } from "./format";

describe("pctFromFraction — 입력은 분수(0~1)다", () => {
  it("0.05를 5.00%로 (×100 한다)", () => {
    expect(pctFromFraction(0.05)).toBe("5.00%");
  });
  it("자릿수 지정", () => {
    expect(pctFromFraction(0.0512, 1)).toBe("5.1%");
  });
  it("1.0은 100%", () => {
    expect(pctFromFraction(1)).toBe("100.00%");
  });
  it("null/undefined는 NO_DATA", () => {
    expect(pctFromFraction(null)).toBe(NO_DATA);
    expect(pctFromFraction(undefined)).toBe(NO_DATA);
  });
  it("0은 NO_DATA가 아니라 0.00% — 0과 '없음'은 다르다(D-47-h)", () => {
    expect(pctFromFraction(0)).toBe("0.00%");
  });
});

describe("num", () => {
  it("천단위 구분", () => expect(num(91005)).toBe("91,005"));
  it("null은 NO_DATA", () => expect(num(null)).toBe(NO_DATA));
  it("0은 '0' — 0과 '없음'은 다르다", () => expect(num(0)).toBe("0"));
});

describe("won", () => {
  it("원 붙임", () => expect(won(204135)).toBe("204,135원"));
  it("null은 NO_DATA", () => expect(won(null)).toBe(NO_DATA));
});

describe("roasX", () => {
  it("배 붙임", () => expect(roasX(2.62)).toBe("2.62배"));
  it("null은 NO_DATA", () => expect(roasX(null)).toBe(NO_DATA));
});

describe("isoKST", () => {
  it("KST 날짜 문자열", () => {
    // 2026-07-17 00:30 KST = 2026-07-16 15:30 UTC
    expect(isoKST(new Date("2026-07-16T15:30:00Z"))).toBe("2026-07-17");
  });
});

describe("NO_DATA", () => {
  it("em-dash — 하이픈이 아니다(§8-2 의도적 통일)", () => {
    expect(NO_DATA).toBe("—");
  });
});

// ── D-NAO-47: 이 파일이 프론트 유일 테스트라 여기 남긴다 ──
// ★타입 검증은 `npx tsc -b`로 해야 한다. `npx tsc --noEmit`은 **아무것도 검사하지 않는다** —
//   tsconfig.json이 {"files": [], "references": [...]} 형태(solution-style)라 bare tsc는
//   파일 0개를 보고 조용히 성공한다. 실측(2026-07-17): src/에 `const x: number = "string"`을
//   넣고 `npx tsc --noEmit` → 출력 없음(통과). 같은 파일에 `npx tsc -b` → TS2322 정상 검출.
//   "타입 통과했습니다"를 --noEmit으로 주장하면 위약이다.
