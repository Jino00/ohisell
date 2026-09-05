// naverAutoUpApiWire.test.ts — D-NAO-287 적대 리뷰 1R [P1-1] 처분.
//
// ★왜 이 파일이 따로 있나: 표면 테스트(`naverAdAutoUpResetSurface.test.tsx`)는
//   `vi.mock("../lib/api", …)`로 이 두 함수를 **통째로 대체**한다. 그래서 그 파일은
//   「버튼 클릭이 JS 함수 호출까지 닿는가」만 재고, **「그 함수가 올바른 URL·바디로
//   백엔드에 닿는가」는 아무도 재지 않았다.** 적대 리뷰가 변이 2종으로 실증했다:
//     · URL `/auto-up-base/reset` → `/auto-up-base/resett` ⇒ 전건 초록(생존)
//     · 바디 키 `entity_id` → `entityId`   ⇒ 전건 초록(생존). 실제 FastAPI엔 **422**.
//   백엔드 테스트는 TestClient로 라우트를 직접 때리므로 이 갭을 원리적으로 못 잡는다.
//   ⇒ 여기서는 `../lib/api`를 mock하지 않고 **`fetch`를 세워** 와이어의 실제 모양을 잰다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { getNaverAutoUpCeiling, resetNaverAutoUpBase } from "./api";

function stubFetch(payload: unknown) {
  const spy = vi.fn(async () => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload),
  })) as unknown as typeof fetch;
  vi.stubGlobal("fetch", spy);
  return spy as unknown as ReturnType<typeof vi.fn>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("자동 상향 여력 API 와이어", () => {
  it("★조회는 정확히 /api/naver/ad/auto-up-ceiling 을 친다 (URL 오타 변이 표적)", async () => {
    const spy = stubFetch({ rows: [] });
    await getNaverAutoUpCeiling();
    expect(spy).toHaveBeenCalledTimes(1);
    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/naver/ad/auto-up-ceiling");
    expect(url.endsWith("/api/naver/ad/auto-up-ceiling")).toBe(true);
  });

  it("★리셋은 POST로 /api/naver/ad/auto-up-base/reset 을 친다 (URL 오타 변이 표적)", async () => {
    const spy = stubFetch({ entity_id: "nad-1" });
    await resetNaverAutoUpBase({ entityId: "nad-1", reason: "복귀" });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/naver/ad/auto-up-base/reset")).toBe(true);
    expect(init.method).toBe("POST");
  });

  it("★바디 키는 백엔드 pydantic 필드명 그대로다 — entity_id·reason·actor (키 오타 변이 표적)", async () => {
    const spy = stubFetch({ entity_id: "nad-1" });
    await resetNaverAutoUpBase({ entityId: "nad-1", reason: "굳은 소재 복귀", actor: "Jino" });
    const [, init] = spy.mock.calls[0] as [string, RequestInit];
    // 백엔드 AutoUpBaseResetIn(entity_id, reason, actor) — 어긋나면 라이브에서 422다.
    expect(JSON.parse(String(init.body))).toEqual({
      entity_id: "nad-1", reason: "굳은 소재 복귀", actor: "Jino",
    });
  });

  it("서버 오류 문구는 그대로 올라온다 — 사유 필수 400·라이브 조회 실패 502를 화면이 말해야 한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false, status: 400,
      text: async () => '{"detail":"사유(reason)는 필수입니다 — 이 입구의 목적이 감사 기록입니다"}',
    })) as unknown as typeof fetch);
    await expect(resetNaverAutoUpBase({ entityId: "nad-1", reason: "" }))
      .rejects.toThrow(/사유\(reason\)는 필수입니다/);
  });
});
