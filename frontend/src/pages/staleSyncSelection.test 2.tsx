// @vitest-environment jsdom
//
// staleSyncSelection.test.tsx — issue #235 회귀.
//
// 두 페이지는 마운트 시 `await syncRealtime()` 뒤 재조회한다. 그 재조회가 **마운트 시점
// 클로저**의 기간/계정을 쓰면, 대기 중 사용자가 고른 기간을 옛 값으로 덮는다.
// 창이 좁지 않다 — prod 실측 POST /api/sync/realtime = 32.1 / 29.9 / 32.9초(2026-08-07 13:01 KST).
//
// ★`reqSeq` 가드로는 이 실패 모드를 못 막는다: reqSeq는 응답이 역순으로 도착하는 것을 막지만,
//   이 버그는 **나중에 발행된 요청이 옛 인자를 든** 것이라 seq가 더 커서 오히려 이긴다.
//   그래서 검증 대상은 "누가 이겼나"가 아니라 **재조회에 실린 인자**다(1·2번 테스트).
//   3번 테스트는 AdReport에 새로 들여온 reqSeq 가드(역순 도착) 쪽을 본다.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

// 조회 promise는 기본적으로 **영원히 pending** — 이 테스트가 보는 것은 렌더 결과가 아니라
// "어떤 인자로 요청했는가"이고, resolve하면 페이지 전체 렌더 트리를 만족시켜야 한다.
// vi.mock 팩토리는 hoist되므로 상태는 vi.hoisted로 만든다.
const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    cc: [] as string[][],   // fetchCommandCenter(from, to, account) 호출 인자
    ad: [] as string[][],   // fetchCoupangAdReport(from, to) 호출 인자
    releaseSync: () => {},  // 동기화 완료 트리거
    sync: null as Promise<unknown> | null,
    // 3번 테스트만 응답 타이밍을 직접 쥔다. null이면 pending.
    adImpl: null as null | ((f: string, t: string) => Promise<unknown>),
  };
});

// 부분 목: streamRefresh.ts가 api의 다른 export들(갱신 상태 페처)을 모듈 로드 시점에 참조하므로
// 통짜 목은 못 쓴다. 이 테스트가 건드리는 함수만 덮는다.
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  syncRealtime: () => h.sync,
  fetchCommandCenter: (f: string, t: string, a: string) => { h.cc.push([f, t, a]); return h.pending(); },
  fetchRevenueReconcile: () => h.pending(),
  fetchRocketOverview: () => h.pending(),
  fetchRocketCostMapUnmapped: () => h.pending(),
  fetchRocketCostMap: () => h.pending(),
  upsertRocketCostMap: () => h.pending(),
  deleteRocketCostMap: () => h.pending(),
  fetchRocketPromoPnl: () => h.pending(),
  patchPromotionManual: () => h.pending(),
  fetchCoupangAdReport: (f: string, t: string) => {
    h.ad.push([f, t]);
    return h.adImpl ? h.adImpl(f, t) : h.pending();
  },
}));

import CommandCenter from "./CommandCenter";
import AdReport from "./AdReport";

const APPLIED_FROM = "2026-01-01"; // 어느 날 돌려도 기본 기간과 겹치지 않는 고정 과거 날짜

beforeEach(() => {
  h.cc = [];
  h.ad = [];
  h.adImpl = null;
  h.sync = new Promise((resolve) => { h.releaseSync = () => resolve(undefined); });
});

afterEach(() => cleanup());

/** 첫 번째 date 인풋을 APPLIED_FROM으로 바꾸고 「조회」를 누른다 = 사용자의 기간 적용. */
function applyPeriod(container: HTMLElement) {
  const dateInputs = container.querySelectorAll('input[type="date"]');
  expect(dateInputs.length).toBeGreaterThanOrEqual(2);
  fireEvent.change(dateInputs[0], { target: { value: APPLIED_FROM } });
  fireEvent.click(screen.getByRole("button", { name: "조회" }));
}

describe("issue #235 — 마운트 동기화 완료가 사용자의 선택을 덮지 않는다", () => {
  it("CommandCenter: 동기화 대기 중 바꾼 기간·계정이 완료 후에도 유지된다", async () => {
    const { container } = render(<CommandCenter />);

    // 동기화가 끝나기 전에는 아직 아무것도 조회하지 않는다(=대기 창이 열려 있다).
    expect(h.cc).toHaveLength(0);

    applyPeriod(container);
    fireEvent.click(screen.getByRole("button", { name: "오하이테크" }));

    const applied = h.cc.at(-1)!;
    expect(applied[0]).toBe(APPLIED_FROM);
    expect(applied[2]).toBe("COUPANG_WING2");

    const before = h.cc.length;
    h.releaseSync();

    // 동기화 완료가 재조회를 한 번 더 발행한다 — 그 인자가 '방금 적용한 선택'이어야 한다.
    await waitFor(() => expect(h.cc.length).toBeGreaterThan(before));
    expect(h.cc.at(-1)).toEqual(applied);
  });

  it("AdReport: 동기화 대기 중 바꾼 기간이 완료 후에도 유지된다", async () => {
    const { container } = render(<AdReport />);

    expect(h.ad).toHaveLength(0);

    applyPeriod(container);

    const applied = h.ad.at(-1)!;
    expect(applied[0]).toBe(APPLIED_FROM);

    const before = h.ad.length;
    h.releaseSync();

    await waitFor(() => expect(h.ad.length).toBeGreaterThan(before));
    expect(h.ad.at(-1)).toEqual(applied);
  });

  it("AdReport: 옛 요청의 응답이 늦게 도착해도 최신 선택의 표를 덮지 않는다", async () => {
    const resolvers: Array<(v: unknown) => void> = [];
    h.adImpl = () => new Promise((resolve) => { resolvers.push(resolve); });

    const { container } = render(<AdReport />);
    h.releaseSync();
    await waitFor(() => expect(resolvers.length).toBe(1)); // 마운트 동기화 완료분(옛 기간)

    applyPeriod(container);                                 // 2번째 = 최신 선택
    await waitFor(() => expect(resolvers.length).toBe(2));

    const row = (label: string) => ({
      sell_type: label, impressions: 1, clicks: 0, ctr: 0, orders: 0, sales_qty: 0,
      cvr: 0, ad_spend: 0, conversion_revenue: 0, roas: 0,
      roas_basis: "our_revenue", roas_basis_label: "우리 매출",
    });
    const mk = (label: string) => ({ items: [row(label)], total: row("합계") });

    resolvers[1](mk("NEW"));
    await waitFor(() => expect(screen.queryAllByText("NEW").length).toBeGreaterThan(0));

    resolvers[0](mk("OLD")); // 옛 요청이 뒤늦게 도착 — 무시되어야 한다
    await waitFor(() => expect(screen.queryAllByText("NEW").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("OLD")).toHaveLength(0);
  });
});
