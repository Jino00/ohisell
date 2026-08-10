// rocketCostMapExclude.test.ts — 「연결 안 함」 요청이 **사유를 싣는가**.
//
// 왜(2026-08-10 적대 리뷰 P2-7): 백엔드는 사유 없는 `excluded`를 422로 거부한다. 그래서
//   화면이 `note`를 빼먹으면 **모든 제외 클릭이 실패**하는데, 백엔드 테스트는 못 잡는다 —
//   백엔드는 정상 동작하기 때문이다. 그 사이 사용자는 «❌ 제외 실패»만 본다.
// ★그리고 `match_method`가 자동 계열이면 역시 422다 — 여기서 'manual'을 박는지도 본다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { excludeRocketCostMap } from "./api";

function stubFetch() {
  const spy = vi.fn().mockResolvedValue({
    ok: true, status: 200, text: async () => JSON.stringify({ product_number: "X" }),
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("excludeRocketCostMap", () => {
  it("사유와 manual을 실어 보낸다", async () => {
    const spy = stubFetch();
    await excludeRocketCostMap("59459681", "  샘플로만 나가는 물건  ");
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("/api/coupang/ops/rocket/cost-map");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toMatchObject({
      product_number: "59459681",
      status: "excluded",
      match_method: "manual",       // ★자동 계열이면 백엔드가 422로 막는다
      note: "샘플로만 나가는 물건",  // ★앞뒤 공백은 다듬어 보낸다
    });
  });

  it("사유가 비면 **요청 자체를 안 보낸다** — 서버 왕복 없이 여기서 막는다", async () => {
    const spy = stubFetch();
    await expect(excludeRocketCostMap("59459681", "   ")).rejects.toThrow(/사유/);
    expect(spy).not.toHaveBeenCalled();
  });
});
