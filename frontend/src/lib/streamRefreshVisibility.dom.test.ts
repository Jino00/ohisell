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

// ── 종료 보장 (2026-08-23 적대 리뷰 P1-1) ─────────────────────────────────────
//
// ★W5가 상한을 «깨어 있던 시간»으로 바꾸면서 **끝난다는 보장이 사라졌다**: 숨어 있는 동안
//   `now()`와 `hiddenMs()`가 같은 속도로 자라 깨어 있던 시간이 상수로 굳는다. 탭이 끝내
//   돌아오지 않으면 루프가 영원히 돈다(리뷰 실측: 가상 벽시계 83시간·폴 10만 회).
//   데스크톱 Chrome 백그라운드 탭은 setTimeout을 «멈추는» 게 아니라 ~1분에 1회로 스로틀하므로
//   그동안 6레인이 prod를 계속 두드리고, 프로미스가 정착하지 않아 패널은 「Mac이 수집 중…」에
//   박제된다 — prod가 fresh인데 화면은 영원히 진행 중(합격 ④ 위반).
describe("W5 종료 보장 — 숨은 채 돌아오지 않아도 반드시 끝난다", () => {
  it("탭이 영영 안 돌아와도 절대 천장에서 정착한다", async () => {
    let t = 0;
    let polls = 0;
    const spec: StreamRefreshSpec = {
      key: "t", label: "테스트", account: "오하이테크(A01029796)",
      getStatus: async () => {
        polls += 1;
        if (polls === 1) return S({ requested: false, attempt_count: 0 });
        if (polls === 2) setVisibility("hidden"); // 백그라운드로 가고 돌아오지 않는다
        if (polls > 5000) throw new Error("BAILOUT — 루프가 안 끝난다");
        return S({ requested: true, attempt_count: 1, in_flight: true });
      },
      request: async () => {},
    };

    const out = await runStreamRefresh(spec, {
      now: () => t,
      sleep: async (ms: number) => { t += ms; },
      pollMs: 3000,
    });

    expect(out.state).toBe("no_response");
    expect(polls).toBeLessThan(5000);
    // 정상 경로(깨어 있는 탭)의 T_max 600초는 넘되, 무한은 아니다.
    expect(t).toBeGreaterThan(600000);
    expect(t).toBeLessThanOrEqual(1800000 + 3000);
  });

  it("복귀 이벤트가 없어도 «진행 중인» hidden 구간을 계상한다", async () => {
    // ★리뷰어 변이 M5(`hiddenMs: () => total`)가 전건 초록으로 생존했던 자리다.
    //   숨은 채 폴이 계속 도는 동안(Chrome 스로틀) 그 시간을 계상하지 않으면, 깨어 있던 시간이
    //   벽시계처럼 자라 T_max에서 「응답 없음」을 낸다 — 정작 그 직후 성공이 도착하는데도.
    let t = 0;
    let polls = 0;
    const spec: StreamRefreshSpec = {
      key: "t", label: "테스트", account: "오하이테크(A01029796)",
      getStatus: async () => {
        polls += 1;
        if (polls === 1) return S({ requested: false, attempt_count: 0 });
        if (polls === 2) setVisibility("hidden"); // 이후 계속 숨어 있다(복귀 이벤트 없음)
        // 숨은 동안 벽시계가 폴마다 1분씩 흐른다(백그라운드 스로틀 모양).
        if (polls >= 3) t += 60000;
        // 벽시계 12분쯤에 Mac이 완주한다 — T_max(600초)를 이미 넘긴 시점이다.
        if (t >= 720000) return S({ requested: false, attempt_count: 1, last_success_at: "T1" });
        return S({ requested: true, attempt_count: 1, in_flight: true });
      },
      request: async () => {},
    };

    const out = await runStreamRefresh(spec, {
      now: () => t,
      sleep: async (ms: number) => { t += ms; },
      pollMs: 3000,
    });

    expect(out).toEqual({ state: "done" });
    expect(t).toBeGreaterThan(600000); // 벽시계로는 마감을 넘겼는데도 성공을 잡았다
  });
});
