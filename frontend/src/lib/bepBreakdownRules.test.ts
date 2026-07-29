// bepBreakdownRules.test.ts — ⑤BEP 구성 순수 로직 가드(D-NAO-104 Phase 3).
import { describe, it, expect } from "vitest";
import {
  stripBoldMarkers, marketBidTone,
  marketBidDeviceLabel, shortObservedDate, marketBidCaption,
} from "./bepBreakdownRules";

describe("stripBoldMarkers — 마크다운 굵게 제거", () => {
  it("굵게 마커가 없으면 원문 그대로", () => {
    expect(stripBoldMarkers("이 상품을 파는 광고 한 곳 기준입니다.")).toBe(
      "이 상품을 파는 광고 한 곳 기준입니다.",
    );
  });

  it("한 쌍의 ** 를 제거한다", () => {
    expect(stripBoldMarkers("그중 **가장 빡빡한 쪽**에 맞춘 값입니다.")).toBe(
      "그중 가장 빡빡한 쪽에 맞춘 값입니다.",
    );
  });

  it("여러 쌍을 전부 제거한다", () => {
    expect(stripBoldMarkers("**A**는 **B**보다 큽니다.")).toBe("A는 B보다 큽니다.");
  });

  it("HTML 태그로 바뀌지 않는다(순수 텍스트만 남는다)", () => {
    const out = stripBoldMarkers("**강조**된 문장");
    expect(out).not.toContain("<");
    expect(out).not.toContain(">");
    expect(out).toBe("강조된 문장");
  });

  it("빈 문자열은 빈 문자열", () => {
    expect(stripBoldMarkers("")).toBe("");
  });
});

describe("marketBidTone — 시장가 vs 상한 톤 판정", () => {
  it("시장가가 상한보다 비싸면 bad(손해)", () => {
    expect(marketBidTone(2280, 1397)).toBe("bad");
  });

  it("시장가가 상한 이내면 good", () => {
    expect(marketBidTone(1200, 1397)).toBe("good");
  });

  it("시장가가 상한과 정확히 같으면 good(손해는 아니다)", () => {
    expect(marketBidTone(1397, 1397)).toBe("good");
  });

  it("시장가를 모르면 idle", () => {
    expect(marketBidTone(null, 1397)).toBe("idle");
  });

  it("상한을 모르면 idle", () => {
    expect(marketBidTone(2280, null)).toBe("idle");
  });

  it("둘 다 모르면 idle", () => {
    expect(marketBidTone(null, null)).toBe("idle");
  });

  it("undefined도 모름으로 취급한다", () => {
    expect(marketBidTone(undefined, 1397)).toBe("idle");
    expect(marketBidTone(2280, undefined)).toBe("idle");
  });

  it("ceilingIsBorrowed 인자를 생략하면 기존과 동일하게 동작한다", () => {
    expect(marketBidTone(2280, 1397)).toBe("bad");
    expect(marketBidTone(1200, 1397)).toBe("good");
  });

  it("상한이 계정 평균을 빌린 값이면(ceilingIsBorrowed=true) 손해였을 값도 idle", () => {
    expect(marketBidTone(2280, 1397, true)).toBe("idle");
  });

  it("ceilingIsBorrowed=true면 이득이었을 값도 idle(색으로 확신을 주지 않는다)", () => {
    expect(marketBidTone(1200, 1397, true)).toBe("idle");
  });

  it("ceilingIsBorrowed=false면 기존 판정 그대로", () => {
    expect(marketBidTone(2280, 1397, false)).toBe("bad");
    expect(marketBidTone(1200, 1397, false)).toBe("good");
  });

  it("값을 모르면 ceilingIsBorrowed와 무관하게 idle", () => {
    expect(marketBidTone(null, 1397, true)).toBe("idle");
    expect(marketBidTone(2280, null, true)).toBe("idle");
  });
});

describe("marketBidDeviceLabel — 기기 코드 표기 변환", () => {
  it("MOBILE은 모바일", () => {
    expect(marketBidDeviceLabel("MOBILE")).toBe("모바일");
  });

  it("PC는 PC", () => {
    expect(marketBidDeviceLabel("PC")).toBe("PC");
  });

  it("알 수 없는 값은 빈 문자열", () => {
    expect(marketBidDeviceLabel("TABLET")).toBe("");
  });

  it("null/undefined도 빈 문자열", () => {
    expect(marketBidDeviceLabel(null)).toBe("");
    expect(marketBidDeviceLabel(undefined)).toBe("");
  });
});

describe("shortObservedDate — 관측일 축약 표기", () => {
  it("YYYY-MM-DD를 MM-DD로 줄인다", () => {
    expect(shortObservedDate("2026-07-28")).toBe("07-28");
  });

  it("형식이 다르면 추측하지 않고 원문 그대로", () => {
    expect(shortObservedDate("2026-07")).toBe("2026-07");
    expect(shortObservedDate("not-a-date")).toBe("not-a-date");
  });
});

describe("marketBidCaption — 시장가 셀 캡션(기기 + 관측일)", () => {
  it("기기+관측일 모두 있으면 '모바일 · 07-28 관측'", () => {
    expect(marketBidCaption("MOBILE", "2026-07-28")).toBe("모바일 · 07-28 관측");
  });

  it("PC 기기면 'PC · 07-28 관측'", () => {
    expect(marketBidCaption("PC", "2026-07-28")).toBe("PC · 07-28 관측");
  });

  it("기기를 모르면 관측일만", () => {
    expect(marketBidCaption(null, "2026-07-28")).toBe("07-28 관측");
  });

  it("관측일이 없으면(=market_bid null) 빈 문자열 — 아무것도 렌더하지 않는다", () => {
    expect(marketBidCaption("MOBILE", null)).toBe("");
    expect(marketBidCaption(null, null)).toBe("");
    expect(marketBidCaption(null, undefined)).toBe("");
  });
});
