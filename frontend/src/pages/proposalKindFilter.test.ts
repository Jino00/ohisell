// proposalKindFilter.test.ts — 제안 카드 유형 필터 매핑 가드(D-NAO-47).
// ★존재 이유: 콘솔의 유형 세그먼트(실행형/정보성/전부)가 백엔드 GET /proposals의
//   `informational` 쿼리 파라미터로 정확히 매핑돼야 한다. 매핑이 뒤집히면(actionable→true 등)
//   실행형 탭이 정보성만 보여주고, 정보성 경보가 실행형을 파묻던 라이브 버그가 되살아난다.
//   백엔드 계약: true=정보성만 / false=실행형만 / undefined(생략)=전부.
import { describe, it, expect } from "vitest";
import { proposalKindToInformational } from "./NaverAdOptimizationConsole";

describe("proposalKindToInformational — 유형 → informational 쿼리 파라미터", () => {
  it("actionable(실행형) → false (백엔드가 정보성 유형을 제외)", () => {
    expect(proposalKindToInformational("actionable")).toBe(false);
  });

  it("informational(정보성) → true (백엔드가 정보성 유형만)", () => {
    expect(proposalKindToInformational("informational")).toBe(true);
  });

  it("all(전부) → undefined (파라미터 생략 = 필터 없음)", () => {
    expect(proposalKindToInformational("all")).toBeUndefined();
  });

  it("기본값 actionable은 false여야 콘솔이 '결정할 것'을 먼저 보인다(회귀 고정)", () => {
    // 콘솔 초기 상태 proposalKind='actionable' → 반드시 실행형 질의(false)로 나가야 한다.
    expect(proposalKindToInformational("actionable")).toBe(false);
    // 정보성(true)이 기본이 되면 라이브 버그 재발 → 절대 아님.
    expect(proposalKindToInformational("actionable")).not.toBe(true);
  });
});
