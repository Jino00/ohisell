// @vitest-environment jsdom
//
// costPurchasedPriceReachable.test.tsx — 매입품 단가 축에 **손이 닿는가** (D-CPP-63 S1 3/3)
//
// ★`costPurchasedPriceSurface.test.tsx`는 패널을 «직접» 렌더해 그 안의 내용을 잰다.
//   그 테스트들은 **탭이 CostPage에서 통째로 빠져도 전부 초록이다** — 이 저장소가 반복해
//   밟은 「함수는 값을 만드는데 사람은 못 본다」의 정확한 모양이고, 그래서 파일을 나눠
//   여기서 «도달 경로»만 따로 지킨다: `/cost`에서 App을 통째로 렌더하고 탭을 실제로 누른다.
//
// 이 파일이 죽여야 하는 변이 둘:
//   ① 탭바 배열에서 ["purchased", "매입품 단가"]를 지운다 → 누를 곳이 사라진다
//   ② `{tab === "purchased" ? <CostPurchasedPricePanel /> : null}` 렌더를 지운다
//      → 탭은 눌리는데 아무것도 안 뜬다
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchCostMaterials: vi.fn(async () => ({ items: [] })),
    fetchCostLedgerMaterialLines: vi.fn(async () => ({ items: [] })),
    fetchCostSettings: vi.fn(async () => ({
      items: [
        { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
        { key: "standard_price_rule", value: "latest", confirmed: true, note: null, updated_at: null },
      ],
    })),
    fetchCostRecipes: vi.fn(async () => ({ items: [] })),
    fetchCostBoard: vi.fn(async () => ({
      items: [],
      sku_count: 0,
      computed_count: 0,
      uncomputed_count: 0,
      recipe_count: 0,
      approved_recipe_count: 0,
    })),
    fetchCostTableCensus: vi.fn(async () => ({ items: [] })),
    fetchCostSettingHistory: vi.fn(async () => ({ items: [] })),
    fetchCostAutoRefreshRuns: vi.fn(async () => ({ items: [] })),
    fetchCostAutoRefreshQueue: vi.fn(async () => ({ items: [] })),
    getSchedulerHealth: vi.fn(async () => ({ healthy: true })),
    getAdCostCookieStatus: vi.fn(async () => ({})),
    getCollectionStatus: vi.fn(async () => ({ streams: [] })),
    fetchApi: vi.fn(async () => ({ jobs: [], items: [] })),
    // 이 축이 실제로 부르는 것 — 보드가 뜨는 것이 「패널이 살아 있다」의 증거다
    fetchPurchasedBoard: vi.fn(async () => ({
      candidates: 473,
      grounded: 12,
      held_blank: 34,
      unconfirmed: 427,
    })),
  };
});

const fetchSpy = vi.fn(async () => ({
  ok: true,
  status: 200,
  text: async () => "{}",
  json: async () => ({}),
})) as unknown as typeof fetch;

beforeEach(() => {
  vi.stubGlobal("fetch", fetchSpy);
  window.history.pushState({}, "", "/cost");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("원가 화면에 「매입품 단가」 탭이 있고, 누르면 패널과 보드가 실제로 뜬다", async () => {
  const { default: App } = await import("../App");
  render(<App />);
  await screen.findByRole("heading", { name: /원가/ });

  // ① 탭이 탭바에 실재한다
  const tab = screen.getByRole("button", { name: "매입품 단가" });
  expect(tab).toBeTruthy();

  // 누르기 «전»에는 패널이 없다 (탭 전환이 실제로 일을 한다는 증거)
  expect(screen.queryByTestId("purchased-panel")).toBeNull();

  fireEvent.click(tab);

  // ② 누른 뒤 패널이 뜨고, 보드 숫자가 실제 값으로 들어찬다
  expect(await screen.findByTestId("purchased-panel")).toBeTruthy();
  const board = await screen.findByTestId("purchased-board");
  expect(board.textContent).toMatch(/473/);
  expect(board.textContent).toMatch(/34/);
  expect(screen.getByTestId("purchased-file")).toBeTruthy();
});
