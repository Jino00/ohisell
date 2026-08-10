// guardrailParamsSave.test.ts — 전체 치환의 함정을 못 박는다 (D-NAO-172 P1)
//
// 이 파일이 지키는 것 하나: **하나를 고쳐 저장했을 때 나머지가 조용히 기본값으로 돌아가지 않는가.**
// PUT이 전체 치환이라, 이 조립 규칙이 깨지면 사람은 A만 고쳤는데 B가 사라진다 —
// 봉투가 아무도 모르게 바뀌는 것이 이 기능이 막으려던 사고 그 자체다.
import { describe, expect, it } from "vitest";

import { buildGuardrailSaveBody, type GuardrailParamRow } from "./guardrailParamsSave";

const row = (
  key: string,
  value: number,
  source: "db" | "code",
  rejected = false,
): GuardrailParamRow => ({ key, label: key, value, source, rejected });

describe("buildGuardrailSaveBody", () => {
  it("★안 건드린 db 값을 함께 보내 전체 치환에 쓸려 사라지지 않게 한다", () => {
    const params = [
      row("cooldown_hours", 4, "db"),          // 이미 설정돼 있고 이번엔 안 건드림
      row("max_change_pct", 0.15, "code"),     // 사람이 지금 고침
    ];
    const res = buildGuardrailSaveBody(params, { max_change_pct: "0.2" }, { max_change_pct: true });
    expect(res).toEqual({ ok: true, body: { cooldown_hours: 4, max_change_pct: 0.2 } });
  });

  it("코드 기본값(안 건드린 것)은 보내지 않는다 — 설정하지 않은 것을 설정한 것으로 만들지 않는다", () => {
    const params = [row("cooldown_hours", 2, "code"), row("max_change_pct", 0.15, "code")];
    const res = buildGuardrailSaveBody(params, {}, {});
    expect(res).toEqual({ ok: true, body: {} });
  });

  it("rejected 행은 되돌려 보내지 않는다 — 범위 밖이라 폴백된 값은 저장 때 정리되는 게 맞다", () => {
    const params = [row("max_auto_up_multiple", 2.0, "code", true), row("cooldown_hours", 3, "db")];
    const res = buildGuardrailSaveBody(params, {}, {});
    expect(res).toEqual({ ok: true, body: { cooldown_hours: 3 } });
  });

  it("숫자가 아닌 입력은 서버에 보내기 전에 막고 어느 항목인지 알려준다", () => {
    const params = [row("cooldown_hours", 2, "code")];
    const res = buildGuardrailSaveBody(params, { cooldown_hours: "두시간" }, { cooldown_hours: true });
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error).toContain("cooldown_hours");
  });

  it("빈 입력도 막는다 — 빈 칸을 0으로 읽으면 쿨다운 0이 되어 진동 방어가 풀린다", () => {
    const params = [row("cooldown_hours", 2, "code")];
    const res = buildGuardrailSaveBody(params, { cooldown_hours: "  " }, { cooldown_hours: true });
    expect(res.ok).toBe(false);
  });

  it("범위 밖 값은 **막지 않고** 서버로 보낸다 — 거부 사유는 서버가 말해야 한다", () => {
    // 프론트가 min/max를 복제하면 두 곳이 갈라지고, 갈라지면 사람은 왜 막혔는지 모른다.
    const params = [row("max_auto_up_multiple", 2.0, "code")];
    const res = buildGuardrailSaveBody(
      params, { max_auto_up_multiple: "99" }, { max_auto_up_multiple: true });
    expect(res).toEqual({ ok: true, body: { max_auto_up_multiple: 99 } });
  });
});
