// naverSymmetryFormat.test.ts — B5 대칭·탐색 관측 표시 순수 함수 회귀 (D-NAO-247 점화 계약)
import { describe, expect, it } from "vitest";

import {
  formatDirectionCount,
  formatShare,
  isBrakeOnlyDrift,
} from "./naverSymmetryFormat";

describe("formatShare", () => {
  it("null은 «표본 없음»이다 — 0.0%(측정했더니 0)와 절대 같은 문자열이 아니다", () => {
    expect(formatShare(null)).toBe("표본 없음");
  });

  it("0은 «측정했더니 0»이라 0.0%로 정직하게 낸다(지어내지 않는다)", () => {
    expect(formatShare(0)).toBe("0.0%");
  });

  it("일반 비율은 소수 첫째 자리까지 퍼센트로 낸다", () => {
    expect(formatShare(0.6667)).toBe("66.7%");
  });
});

describe("formatDirectionCount", () => {
  it("0건이어도 침묵하지 않고 문자열로 낸다(교훈 #318)", () => {
    expect(formatDirectionCount(0, 0)).toBe("브레이크 0건 · 액셀 0건");
  });

  it("값이 있으면 그대로 낸다", () => {
    expect(formatDirectionCount(3, 1)).toBe("브레이크 3건 · 액셀 1건");
  });
});

describe("isBrakeOnlyDrift", () => {
  it("브레이크만 있고 액셀이 0이면 표류 모양이다(D-NAO-85 재발 방지 — 판정 아님)", () => {
    expect(isBrakeOnlyDrift(5, 0)).toBe(true);
  });

  it("액셀이 0이어도 브레이크도 0이면 표류가 아니다(비교 대상 자체가 없다)", () => {
    expect(isBrakeOnlyDrift(0, 0)).toBe(false);
  });

  it("액셀이 있으면 표류 모양이 아니다", () => {
    expect(isBrakeOnlyDrift(5, 2)).toBe(false);
  });
});
