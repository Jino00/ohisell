// naverParamChangeApproval.test.ts — param_change 승인 값 조립 (D-NAO-249 F1)
import { describe, expect, it } from "vitest";

import { buildApplyValue, prefillApplyValue, type ParamSpecForApproval } from "./naverParamChangeApproval";

const spec = (over: Partial<ParamSpecForApproval> = {}): ParamSpecForApproval => ({
  key: "cooldown_hours",
  value: 4,
  min: 1,
  max: 24,
  ...over,
});

describe("buildApplyValue", () => {
  it("숫자 입력을 그대로 값으로 만든다", () => {
    expect(buildApplyValue("6", spec())).toEqual({ ok: true, value: 6 });
  });

  it("빈 입력은 막는다", () => {
    const res = buildApplyValue("  ", spec());
    expect(res.ok).toBe(false);
  });

  it("숫자가 아닌 입력은 막는다", () => {
    const res = buildApplyValue("여섯시간", spec());
    expect(res.ok).toBe(false);
  });

  it("★lo~hi 밖 값은 보내지 못하게 막는다(서버 400 전에 화면에서 먼저)", () => {
    const res = buildApplyValue("99", spec({ min: 1, max: 24 }));
    expect(res).toEqual({ ok: false, error: "허용 범위 1 ~ 24 밖입니다" });
  });

  it("경계값(min/max 자체)은 허용한다", () => {
    expect(buildApplyValue("1", spec({ min: 1, max: 24 }))).toEqual({ ok: true, value: 1 });
    expect(buildApplyValue("24", spec({ min: 1, max: 24 }))).toEqual({ ok: true, value: 24 });
  });

  it("spec을 못 찾은 경우(봉투 현황판에 없는 키)엔 범위 검증 없이 숫자만 확인한다", () => {
    expect(buildApplyValue("12345", undefined)).toEqual({ ok: true, value: 12345 });
  });
});

describe("prefillApplyValue", () => {
  it("★프리필은 «현재값»이다 — 제안이 권하는 값이 아니다(판사는 키·방향만 정한다)", () => {
    expect(prefillApplyValue(spec({ value: 4 }), undefined)).toBe("4");
  });

  it("사람이 이미 편집한 값이 있으면 그걸 우선한다", () => {
    expect(prefillApplyValue(spec({ value: 4 }), "10")).toBe("10");
  });

  it("spec이 없으면 빈 문자열", () => {
    expect(prefillApplyValue(undefined, undefined)).toBe("");
  });
});
