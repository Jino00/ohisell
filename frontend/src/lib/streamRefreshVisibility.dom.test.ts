// @vitest-environment jsdom
//
// streamRefreshVisibility.dom.test.ts — W5의 «기본 시계»가 실제 브라우저 API 위에서 도는가
// (2026-08-23, 계약 CONTRACT_collection_works_everywhere §3 W5)
//
// ## 왜 node 테스트로는 부족한가
//
// `streamRefresh.test.ts`는 `hiddenMs`를 **주입**해 상한 계산만 검증한다. 그 상태로는
// 「주입 안 했을 때 진짜로 `visibilitychange`를 듣는가」와 「리스너를 치우는가」가 하나도
// 안 지켜진다 — 실제로 자가 변이에서 `dispose()` 제거가 **전건 초록으로 생존**했다.
// 리스너 누수는 조용한 결함이다: 「전체 갱신」을 누를 때마다 6개씩 쌓이고, 아무도 안 운다.
import { afterEach, describe, expect, it, vi } from "vitest";

import { runStreamRefresh, type RefreshStatusLike, type StreamRefreshSpec } from "./streamRefresh";

const S = (o: Partial<RefreshStatusLike>): RefreshStatusLike => ({
  requested: false,
  last_success_at: null,
  last_error_at: null,
  last_error: null,
  ...o,
});

/** 화면 가시성을 바꾸고 이벤트를 쏜다 — 폰 잠금/복귀가 브라우저에서 나는 모양 그대로. */
function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

afterEach(() => {
  setVisibility("visible");
  vi.restoreAllMocks();
});

describe("W5 기본 시계 — document.visibilitychange", () => {
  it("리스너를 반드시 치운다 — 버튼을 누를 때마다 쌓이면 조용한 누수다", async () => {
    const add = vi.spyOn(document, "addEventListener");
    const remove = vi.spyOn(document, "removeEventListener");

    let calls = 0;
    const spec: StreamRefreshSpec = {
      key: "t", label: "테스트", account: "오하이테크(A01029796)",
      getStatus: async () => {
        calls += 1;
        return calls === 1
          ? S({ requested: false, attempt_count: 0 })
          : S({ requested: false, attempt_count: 1, last_success_at: "T1" });
      },
      request: async () => {},
    };

    let t = 0;
    await runStreamRefresh(spec, {
      now: () => t,
      sleep: async (ms: number) => { t += ms; },
      pollMs: 3000, // hiddenMs를 **주입하지 않는다** — 기본 시계가 붙는 경로다
    });

    const added = add.mock.calls.filter(([type]) => type === "visibilitychange").length;
    const removed = remove.mock.calls.filter(([type]) => type === "visibilitychange").length;
    expect(added).toBe(1);
    expect(removed).toBe(added); // 붙인 만큼 뗀다
  });

  it("숨어 있던 시간을 실제로 재서 상한에서 뺀다(주입 없이)", async () => {
    // 폰 잠금 15분을 이벤트로 재현한다. 벽시계로는 T_max(600초)를 훌쩍 넘지만
    // «깨어 있던» 시간은 몇 초뿐이므로 추적이 계속돼 성공을 잡아야 한다.
    let t = 0;
    let calls = 0;
    const spec: StreamRefreshSpec = {
      key: "t", label: "테스트", account: "오하이테크(A01029796)",
      getStatus: async () => {
        calls += 1;
        if (calls === 1) return S({ requested: false, attempt_count: 0 });
        if (calls === 3) {
          setVisibility("hidden");
          t += 900000;          // 잠긴 동안 벽시계만 흐른다
          setVisibility("visible");
        }
        if (calls >= 6) return S({ requested: false, attempt_count: 1, last_success_at: "T1" });
        return S({ requested: true, attempt_count: 1, in_flight: true });
      },
      request: async () => {},
    };

    const out = await runStreamRefresh(spec, {
      now: () => t,
      sleep: async (ms: number) => { t += ms; },
      pollMs: 3000, // hiddenMs 미주입 — 기본 시계가 900초를 스스로 재야 한다
    });

    expect(out).toEqual({ state: "done" });
    expect(t).toBeGreaterThan(900000); // 벽시계로는 이미 마감을 넘긴 시점이었다
  });
});
