// @vitest-environment jsdom
//
// rocketReconRefresh.test.tsx — 통합 대사 화면 '발주 갱신' 버튼 (2026-08-07).
//
// 이 버튼이 지켜야 하는 것 둘:
//  ① 갱신 완료 후 재조회가 **그 시점의 기간**으로 나간다. 갱신은 라이브 실측 227초가 걸리고
//     (요청 19:32:24 → 성공 19:36:11) 그 사이 Jino가 기간을 바꿀 수 있다. 마운트 시점 인자를
//     붙잡은 콜백으로 재조회하면 방금 고른 기간을 옛 기간으로 덮는다 —
//     CommandCenter·AdReport가 실제로 당한 사고와 같은 실패 모드(issue #235, 교훈 #166·#167).
//     ★staleSyncSelection.test.tsx와 같은 이유로 검증 대상은 "누가 이겼나"가 아니라
//     **재조회에 실린 인자**다.
//  ② 실패해도 신선도(마지막 수집 시각)는 다시 읽는다. 실패했을 때야말로 "마지막으로 성공한 게
//     언제인지"가 필요한데, 성공 경로에서만 리로드하면 그때 화면이 낡은 채로 굳는다.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    recon: [] as Array<{ from: string; to: string }>,   // fetchRocketRecon 호출 인자
    statusCalls: 0,                                      // getRocketRefreshStatus 호출 횟수
  };
});

// 부분 목 — streamRefresh.ts가 모듈 로드 시점에 api의 다른 export를 참조하므로 통짜 목은 못 쓴다.
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchRocketRecon: (p: { from: string; to: string }) => {
    h.recon.push({ from: p.from, to: p.to });
    return h.pending();
  },
  getRocketRefreshStatus: () => {
    h.statusCalls += 1;
    return Promise.resolve({
      requested: false, requested_at: null, last_success_at: null, status: "green",
      last_error: null, last_error_at: null, attempt_count: 0, max_attempts: 3,
      claimed_at: null, in_flight: false,
    });
  },
}));

// 갱신 러너는 이 테스트의 대상이 아니다(자체 테스트가 따로 있다). 여기선 **완료 시점만** 쥔다.
const runner = vi.hoisted(() => ({
  release: (_v: unknown) => {},
  run: null as Promise<unknown> | null,
}));
vi.mock("../lib/streamRefresh", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/streamRefresh")>()),
  runStreamRefresh: () => runner.run,
}));

import RocketRecon from "./RocketRecon";

const APPLIED_FROM = "2026-01-01"; // 기본 창(최근 90일)과 절대 겹치지 않는 고정 과거 날짜

beforeEach(() => {
  h.recon = [];
  h.statusCalls = 0;
  runner.run = new Promise((resolve) => { runner.release = resolve; });
});

afterEach(() => cleanup());

function clickRefresh() {
  fireEvent.click(screen.getByRole("button", { name: /발주 갱신/ }));
}

describe("통합 대사 '발주 갱신' 버튼", () => {
  it("갱신 대기 중 바꾼 기간이 완료 후 재조회에도 실린다", async () => {
    const { container } = render(<RocketRecon />);

    await waitFor(() => expect(h.recon.length).toBeGreaterThan(0)); // 마운트 조회
    clickRefresh();

    // 갱신이 도는 동안 기간을 바꾼다 — 실제로 227초짜리 창이 열려 있는 구간.
    const dateInputs = container.querySelectorAll('input[type="date"]');
    expect(dateInputs.length).toBeGreaterThanOrEqual(2);
    fireEvent.change(dateInputs[0], { target: { value: APPLIED_FROM } });

    await waitFor(() => expect(h.recon.at(-1)!.from).toBe(APPLIED_FROM));
    const before = h.recon.length;

    runner.release({ state: "done" });

    // 완료가 재조회를 한 번 더 발행한다 — 그 인자가 '방금 고른 기간'이어야 한다.
    await waitFor(() => expect(h.recon.length).toBeGreaterThan(before));
    expect(h.recon.at(-1)!.from).toBe(APPLIED_FROM);
  });

  it("갱신이 실패해도 신선도를 다시 읽는다", async () => {
    render(<RocketRecon />);
    await waitFor(() => expect(h.statusCalls).toBe(1));

    clickRefresh();
    runner.release({ state: "failed", reason: "로그인 필요" });

    await waitFor(() => expect(h.statusCalls).toBeGreaterThan(1));
    // 실패 사유는 화면에 남는다(조용히 사라지면 다시 누를 이유를 모른다).
    expect(await screen.findByText(/로그인 필요/)).toBeTruthy();
  });

  it("수집 이력이 없으면 0으로 꾸미지 않고 '수집 이력 없음'이라고 말한다", async () => {
    render(<RocketRecon />);
    expect(await screen.findByText(/수집 이력 없음/)).toBeTruthy();
  });
});
