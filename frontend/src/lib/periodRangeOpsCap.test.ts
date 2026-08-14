// periodRangeOpsCap.test.ts — 운영 패널의 기간 상한이 **백엔드와 같은가**.
//
// 적대 리뷰 1R P1-2(2026-08-14): 화면 안내문엔 「조회 구간은 최대 90일입니다」라고 적어놓고
//   프론트 가드는 365일이었다. 91~365일을 고르면 요청이 나가 백엔드가 400을 주고,
//   그 **원문**이 화면에 그대로 뜨는데(`API error 400: {"detail":...}`) 표는 **이전 구간
//   숫자를 계속 보여줬다** — 기간 바와 표가 갈린 상태가 남았다.
//
// `periodRange.ts`가 스스로 못박은 불변식: **백엔드가 막는 입력은 프론트가 먼저 막는다.**
// 상한이 API마다 다르므로(변경 이력 365일 / 매출·손익 90일) 호출부가 상한을 정한다.
import { describe, it, expect } from "vitest";
import { customRangeError, MAX_SPAN_DAYS, OPS_MAX_SPAN_DAYS } from "./periodRange";

const T = "2026-08-14";

/** T에서 n일 전(UTC 고정 — 테스트가 실행 시각에 흔들리면 안 된다). */
function back(n: number): string {
  return new Date(Date.parse(`${T}T00:00:00Z`) - n * 86_400_000).toISOString().slice(0, 10);
}

describe("운영 패널 기간 상한", () => {
  it("백엔드 상한과 같은 값이다 — 두 입구가 다르면 한쪽으로만 들어오는 기간이 생긴다", () => {
    expect(OPS_MAX_SPAN_DAYS).toBe(90);
  });

  it("정확히 90일은 통과한다(경계)", () => {
    expect(customRangeError({ from: back(89), to: T }, T, OPS_MAX_SPAN_DAYS)).toBeNull();
  });

  it("91일은 프론트가 먼저 막는다 — 백엔드 400 원문이 화면에 새지 않게", () => {
    const err = customRangeError({ from: back(90), to: T }, T, OPS_MAX_SPAN_DAYS);
    expect(err).toBe("조회 구간은 최대 90일입니다.");
  });

  it("121일·365일도 막힌다 — 「올해 보기」나 연도 타이핑 중간값이 여기 들어온다", () => {
    for (const n of [120, 364]) {
      expect(customRangeError({ from: back(n), to: T }, T, OPS_MAX_SPAN_DAYS)).not.toBeNull();
    }
  });

  it("상한을 안 주면 종전(365일)대로다 — 변경 이력 화면의 계약을 안 바꾼다", () => {
    expect(MAX_SPAN_DAYS).toBe(365);
    expect(customRangeError({ from: back(120), to: T }, T)).toBeNull();
    expect(customRangeError({ from: back(365), to: T }, T)).toBe("조회 구간은 최대 365일입니다.");
  });

  it("빈 칸·뒤집힘·미래는 상한과 무관하게 그대로 막힌다", () => {
    expect(customRangeError({ from: "", to: T }, T, OPS_MAX_SPAN_DAYS)).toBeTruthy();
    expect(customRangeError({ from: T, to: back(1) }, T, OPS_MAX_SPAN_DAYS)).toBeTruthy();
    expect(customRangeError({ from: T, to: "2027-01-01" }, T, OPS_MAX_SPAN_DAYS)).toBeTruthy();
  });
});
