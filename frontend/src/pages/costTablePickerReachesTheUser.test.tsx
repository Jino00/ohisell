// @vitest-environment jsdom
//
// costTablePickerReachesTheUser.test.tsx — 「원가표 항목 고르기」가 **사람에게 닿는가**
// (계약 A′ 개정 4 · D-CPP-59 · 합격 18~20)
//
// ## 왜 이 파일이 따로 있나
//
// 이 슬라이스가 고치려는 병이 **정확히 「화면이 시키는 조작이 실재하지 않는다」**였다.
// 개정 전 화면은 「후보 N건 — 사람이 고른다」라고 말했는데, 백엔드 엔드포인트 17개 전수와
// 프론트 어디에도 고를 길이 **0건**이었다(계약 §0-E-1 ③).
//
// 그러니 이 슬라이스를 지키는 테스트는 「픽 함수가 값을 만드나」가 아니라
// **「App을 `/cost`에서 통째로 렌더했을 때 사람이 그 버튼에 닿고, 누르면 백엔드가 불리나」**
// 여야 한다. `CostTablePicker`를 직접 렌더해 단언하면 **호출부를 지워도 초록**이고
// (n=6 적대 리뷰 2R이 실증했다: 직접 렌더 테스트는 호출부를 원리적으로 못 잡는다),
// 그러면 이 파일은 자기가 지키려는 것을 안 지킨다.
//
// ## 이 파일이 죽이는 변이 (전부 «표면 절단»)
//
//   PICK-1  `RecipeDetail`의 `{picker}` 렌더 제거          → 패널이 화면에서 사라진다
//   PICK-2  `CostPage`의 `<CostTablePicker>` 호출부 제거    → 주입이 끊긴다
//   PICK-3  `onPick`이 `pickCostTableItem`을 안 부르게      → 눌러도 아무 일이 없다
//   PICK-4  `onAbsent`가 `confirmCostTableAbsent`를 안 부르게
//   PICK-5  「고른 항목」·「없음 확인」 상태 표시 제거        → 침묵과 판정이 다시 뭉개진다
//   PICK-6  `fetchCostTableItems` 호출부 제거               → 목록이 영영 안 온다
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { CostBoard, CostRecipe, CostSetting, CostTableItemList } from "../lib/api";
import {
  confirmCostTableAbsent,
  fetchCostRecipes,
  fetchCostTableItems,
  pickCostTableItem,
  unpickCostTableItem,
} from "../lib/api";

const SETTINGS: CostSetting[] = [
  { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
];

const BOARD: CostBoard = {
  items: [],
  sku_count: 0,
  computed_count: 0,
  uncomputed_count: 0,
  recipe_count: 1,
  approved_recipe_count: 0,
};

const EMPTY_STANDARD = {
  computable: false,
  std_cost_ex_vat: null,
  std_cost_inc_vat: null,
  reason: "구성 라인이 없다 — 레시피가 비어 있다",
  unresolved: [],
  partial_ex_vat: null,
  partial_inc_vat: null,
  line_count: 0,
  lines: [],
};

/** 막힌 레시피 — prod 실측의 flip 4건(id 34·44·51·89)이 이 모양이다. */
const STUCK: CostRecipe = {
  id: 34,
  product_name: "오픽스 Z플립 폴드 외부 사생활보호+내부 지문방지 액정보호필름 4매",
  form_factor: "flip",
  status: "draft",
  source: "excel",
  recipe_kind: "assembly",
  // flip은 옵션명 규칙이 실제로 거는 값이다 — 폴백이 아니다(D-CPP-61 §4-Q2).
  form_source: "rule",
  anomaly_flag: "no_recipe_match",
  approved_at: null,
  match: {
    match_reason: "원가표에 폼팩터 flip · 제품원가 5200.00 인 품목이 없다",
    candidates: [],
    cost_price_mode: "5200.00",
    cost_table_item: null,
    cost_table_section: null,
    excel_total_inc_vat: null,
    sku_count: 6,
    option_count: 6,
  },
  line_count: 0,
  link_count: 6,
  standard: EMPTY_STANDARD,
  picked: {
    state: "none",
    item_id: null,
    item_name: null,
    section: null,
    item_total_inc_vat: null,
    picked_at: null,
    absent_confirmed_at: null,
    absent_note: null,
  },
};

const PICKED: CostRecipe = {
  ...STUCK,
  anomaly_flag: null,
  line_count: 10,
  picked: {
    state: "picked",
    item_id: 7,
    item_name: "지문방지_내부3매+외부3매",
    section: "모바일 필름-플립",
    item_total_inc_vat: "3480.40",
    picked_at: "2026-08-25T17:30:00",
    absent_confirmed_at: null,
    absent_note: null,
  },
};

const ABSENT: CostRecipe = {
  ...STUCK,
  picked: {
    ...STUCK.picked,
    state: "absent",
    absent_confirmed_at: "2026-08-25T17:31:00",
    absent_note: "필름이 아니라 사입 상품이다",
  },
};

/** ★제안이 0건인 목록 — 「제안 없이도 고를 수 있다」가 이 슬라이스의 요점이다. */
const ITEMS: CostTableItemList = {
  recipe_id: 34,
  form_factor: "flip",
  cost_price_mode: "5200.00",
  suggested_count: 0,
  items: [
    {
      id: 7,
      section: "모바일 필름-플립",
      item_name: "지문방지_내부3매+외부3매",
      form_factor: "flip",
      recipe_kind: "assembly",
      total_inc_vat: "3480.40",
      row_number: 42,
      anomalies: null,
      line_count: 10,
      suggested: false,
      picked: false,
    },
    {
      id: 8,
      section: "오타오_강화유리필름",
      item_name: "Glass_Ip17Pro",
      form_factor: null,
      recipe_kind: "imported_goods",
      total_inc_vat: "3226.98",
      row_number: 110,
      anomalies: null,
      line_count: 0,
      suggested: false,
      picked: false,
    },
  ],
};

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchCostMaterials: vi.fn(async () => ({ items: [] })),
    fetchCostLedgerMaterialLines: vi.fn(async () => ({ items: [] })),
    fetchCostSettings: vi.fn(async () => ({ items: SETTINGS })),
    fetchCostRecipes: vi.fn(async () => ({ items: [STUCK] })),
    fetchCostBoard: vi.fn(async () => BOARD),
    // ★D-CPP-60 — `load()`가 이 넷을 항상 부른다. 오버라이드가 없으면 `actual`이 전역
    //   `fetchSpy`(항상 `{}`)를 타 `.items`가 `undefined`가 되고 `AutoRefreshPanel`이 던진다
    //   (costPageReachesTheUser.test.tsx에서 실측 — 같은 원인, 같은 처방).
    fetchCostSettingHistory: vi.fn(async () => ({ items: [] })),
    // ★D-CPP-64 S1 — `load()`가 이력도 부른다. 여기 없으면 `undefined`가 패널로 흘러
    //   `data.items.length`가 던지고 이 파일 전체가 깨진다(위 주석과 같은 함정).
    fetchCostPriceHistory: vi.fn(async () => ({
      items: [], total: 0, started_at: null, empty_reason: "이력이 아직 한 건도 없다",
    })),
    fetchCostAutoRefreshRuns: vi.fn(async () => ({ items: [] })),
    fetchCostAutoRefreshQueue: vi.fn(async () => ({ items: [] })),
    runCostAutoRefreshNow: vi.fn(async () => ({
      run_id: 1,
      trigger: "manual",
      checked: 0,
      updated: 0,
      failed: 0,
      queued: 0,
    })),
    updateCostSetting: vi.fn(async (key: string, body: Record<string, unknown>) => ({
      key,
      value: (body.value as string) ?? "fifo",
      confirmed: (body.confirmed as boolean) ?? false,
      note: (body.note as string | null) ?? null,
      updated_at: null,
      value_changed: false,
      confirmed_changed: false,
    })),
    fetchCostTableItems: vi.fn(async () => ITEMS),
    pickCostTableItem: vi.fn(async () => ({ recipe: PICKED })),
    unpickCostTableItem: vi.fn(async () => ({ recipe: STUCK })),
    confirmCostTableAbsent: vi.fn(async () => ({ recipe: ABSENT })),
    getSchedulerHealth: vi.fn(async () => ({ healthy: true })),
    getAdCostCookieStatus: vi.fn(async () => ({})),
    getCollectionStatus: vi.fn(async () => ({ streams: [] })),
    fetchApi: vi.fn(async () => ({ jobs: [], items: [] })),
  };
});

const fetchSpy = vi.fn(async () => ({
  ok: true,
  status: 200,
  json: async () => ({}),
  text: async () => "",
})) as unknown as typeof fetch;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("fetch", fetchSpy);
  window.history.pushState({}, "", "/cost");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderApp() {
  const { default: App } = await import("../App");
  return render(<App />);
}

async function openRecipeTab() {
  await renderApp();
  await screen.findByRole("heading", { name: /원가/ });
  fireEvent.click(screen.getByRole("button", { name: "레시피" }));
  return await screen.findByTestId("cost-table-picker");
}

describe("★원가표 픽이 사람에게 닿는 경로 — 라우트·페이지·호출부가 한 줄로 이어진다", () => {
  it("PICK-1·2: 레시피 상세에 「원가표 항목 고르기」 패널이 실제로 뜬다", async () => {
    await openRecipeTab();
    expect(screen.getByText(/원가표 항목 고르기/)).toBeTruthy();
    // 「가격이 안 맞아도 고를 수 있다」 — 열쇠가 바뀌었다는 사실이 화면 문장이어야 한다.
    expect(screen.getByText(/가격이 안 맞아도 고를 수 있다/)).toBeTruthy();
  });

  it("PICK-6: 「원가표 목록 보기」를 누르면 목록이 실제로 온다 (합격 18)", async () => {
    await openRecipeTab();
    fireEvent.click(screen.getByTestId("picker-load"));

    await waitFor(() => expect(fetchCostTableItems).toHaveBeenCalledWith(34));
    // 그 폼팩터 전건 — 제안이 0건이어도 목록은 나온다.
    expect(await screen.findByText("지문방지_내부3매+외부3매")).toBeTruthy();
    expect(screen.getByTestId("picker-summary").textContent).toContain("제안 0건");
    // ★수입 완제품(폼팩터 없음)도 목록에 서고, 구성 0줄이라는 사실을 숨기지 않는다.
    expect(screen.getByText(/폼팩터 없음\(수입 완제품·매입품\)/)).toBeTruthy();
    expect(screen.getByText("0줄")).toBeTruthy();
  });

  it("PICK-3: 「고른다」를 누르면 백엔드가 그 레시피·그 항목으로 불린다 (합격 18)", async () => {
    await openRecipeTab();
    fireEvent.click(screen.getByTestId("picker-load"));
    await screen.findByTestId("picker-pick-7");

    fireEvent.click(screen.getByTestId("picker-pick-7"));

    await waitFor(() => expect(pickCostTableItem).toHaveBeenCalledWith(34, 7));
    // ★재조회가 돌아야 화면이 서버 사실을 따라간다 — 안 돌면 사람이 두 번 고른다.
    await waitFor(() => expect(fetchCostRecipes).toHaveBeenCalled());
  });

  it("PICK-4: 「원가표에 없음 — 확인」이 사유와 함께 백엔드로 간다 (합격 19)", async () => {
    await openRecipeTab();
    fireEvent.click(screen.getByTestId("picker-load"));
    await screen.findByTestId("picker-absent");

    fireEvent.change(screen.getByTestId("picker-absent-note"), {
      target: { value: "필름이 아니라 사입 상품이다" },
    });
    fireEvent.click(screen.getByTestId("picker-absent"));

    await waitFor(() =>
      expect(confirmCostTableAbsent).toHaveBeenCalledWith(34, "필름이 아니라 사입 상품이다"),
    );
  });

  it("PICK-5: 「골랐다」와 「없다고 확인함」이 **서로 다른 모양으로** 보인다 (합격 19)", async () => {
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({ items: [PICKED] });
    await openRecipeTab();
    // ★목록 행과 상세 패널 **양쪽**에 뜬다(P1-2 수정 이후) — 개수가 아니라 존재를 잰다.
    expect(screen.getAllByTestId("pick-state-picked").length).toBeGreaterThan(0);
    expect(screen.getByTestId("picker-picked-item").textContent).toContain(
      "지문방지_내부3매+외부3매",
    );
    // ★재업로드가 픽을 안 덮는다는 사실을 화면이 «말한다» (합격 20).
    expect(screen.getByText(/원가표를 다시 올려도 이 구성은 유지된다/)).toBeTruthy();

    cleanup();
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({ items: [ABSENT] });
    await openRecipeTab();
    expect(screen.getAllByTestId("pick-state-absent").length).toBeGreaterThan(0);
    expect(screen.getByTestId("picker-absent-confirmed").textContent).toContain(
      "필름이 아니라 사입 상품이다",
    );
  });

  it("★「아직 아무도 안 봄」에는 배지를 붙이지 않는다 — 침묵이 판정으로 읽히면 안 된다", async () => {
    await openRecipeTab();
    expect(screen.queryByTestId("pick-state-none")).toBeNull();
    expect(screen.queryByTestId("pick-state-absent")).toBeNull();
    expect(screen.queryByTestId("pick-state-picked")).toBeNull();
  });

  it("★핀이 끊기면 화면이 말한다 — 조용한 소실은 미달이다 (합격 20)", async () => {
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({
      items: [{ ...PICKED, picked: { ...PICKED.picked, state: "pin_lost" } }],
    });
    await openRecipeTab();
    expect(screen.getAllByTestId("pick-state-pin_lost").length).toBeGreaterThan(0);
    expect(screen.getByTestId("picker-pin-broken").textContent).toContain("구성은 그대로 두었다");
  });

  // ── 적대 리뷰 1R P1-2 — 합격 19의 «목록» 절반 ──────────────────────────────
  it("★P1-2: 「없음 확인」과 「아직 안 봄」이 **목록에서** 갈린다 (합격 19)", async () => {
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({
      items: [
        { ...ABSENT, id: 34, product_name: "사람이 확인한 것" },
        { ...STUCK, id: 44, product_name: "아무도 안 본 것" },
      ],
    });
    await openRecipeTab();

    // 상세를 열지 않고 «목록 행»에서 구별된다 — 100건에서 상세를 일일이 여는 것은
    // 사실상 「안 보이는 것」과 같다.
    const looked = screen.getByTestId("recipe-row-34");
    const never = screen.getByTestId("recipe-row-44");
    expect(looked.textContent).toContain("원가표에 없음 — 확인함");
    expect(never.textContent).not.toContain("원가표에 없음");
    // 사유·시각도 목록에서 보인다 — 다음 사람이 같은 판단을 다시 안 하도록.
    expect(screen.getByTestId("recipe-row-absent-34").textContent).toContain(
      "필름이 아니라 사입 상품이다",
    );
  });

  it("★P1-2: 핀이 끊긴 레시피도 **목록에서** 사람 말로 보인다 (날문자 금지)", async () => {
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({
      items: [
        {
          ...PICKED,
          id: 51,
          anomaly_flag: "pin_lost",
          picked: { ...PICKED.picked, state: "pin_lost" },
        },
      ],
    });
    await openRecipeTab();
    const row = screen.getByTestId("recipe-row-51");
    expect(row.textContent).toContain("고른 항목이 사라졌다");
    // ★영어 날문자가 화면에 뜨면 안 된다 — D-CPP-56에서 `manual`·`ledger`로 겪은 자리다.
    expect(row.textContent).not.toContain("pin_lost");
  });

  // ── 적대 리뷰 1R P1-1 — 끊긴 핀에서 «시키는 길»이 실제로 열려 있다 ────────────
  it("★P1-1: 핀이 끊긴 상태에서 「끊긴 픽 지우기」가 실재하고 백엔드를 부른다", async () => {
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({
      items: [{ ...PICKED, picked: { ...PICKED.picked, state: "pin_lost" } }],
    });
    await openRecipeTab();

    fireEvent.click(screen.getByTestId("picker-unpick-broken"));
    await waitFor(() => expect(unpickCostTableItem).toHaveBeenCalledWith(34));
  });

  it("★P1-1: 핀이 끊긴 상태에서 「원가표에 없음 — 확인」 버튼이 **뜬다**", async () => {
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({
      items: [{ ...PICKED, picked: { ...PICKED.picked, state: "pin_lost" } }],
    });
    await openRecipeTab();
    fireEvent.click(screen.getByTestId("picker-load"));

    // 화면이 「다시 고르거나 「원가표에 없음」을 확인한다」고 말하므로 그 버튼이 있어야 한다.
    // (서버가 그것을 받는지는 백엔드 테스트가 잰다 —
    //  `test_absent_confirmation_works_when_the_pin_is_broken`.)
    expect(await screen.findByTestId("picker-absent")).toBeTruthy();
  });

  // ── 적대 리뷰 1R P2-1 채택 — 재업로드 «직후» 핀 현황 ────────────────────────
  it("★P2-1: 재업로드 결과에 핀 유지·소실 건수가 뜬다 (합격 20)", async () => {
    const { RecipeImportPanel } = await import("./CostPage");
    render(
      <RecipeImportPanel
        busy={false}
        onImport={() => {}}
        result={{
          recipes_created: 0,
          recipes_updated: 83,
          skipped_approved: 3,
          unmatched: 68,
          materials_seen: 123,
          cost_table_recipes: 40,
          cost_table_anomalies: [],
          mapping_options: 0,
          mapping_anomalies: [],
          groups: 0,
          report: [],
          skipped_pinned: 12,
          cost_table_items: 40,
          pins: { relinked: 11, lost: 1, ambiguous: 0 },
        }}
      />,
    );
    const el = screen.getByTestId("import-pins");
    expect(el.textContent).toContain("유지");
    expect(el.textContent).toContain("대상 사라짐 1");
    expect(el.textContent).toContain("구성은 그대로 두었다");
  });

  it("★승인된 레시피에서는 픽 버튼이 잠긴다 — 승인분을 픽이 갈아치우지 않는다", async () => {
    vi.mocked(fetchCostRecipes).mockResolvedValueOnce({
      items: [{ ...STUCK, status: "approved" }],
    });
    await openRecipeTab();
    fireEvent.click(screen.getByTestId("picker-load"));
    const btn = (await screen.findByTestId("picker-pick-7")) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
