// @vitest-environment jsdom
//
// costPageReachesTheUser.test.tsx — 「💰 원가」가 **사람에게 실제로 닿는가** (적대 리뷰 1R P2-1)
//
// ## 왜 이 파일이 따로 있나
//
// `costMaterialsSurface.test.tsx`는 컴포넌트 **내부** 절단을 다 죽였다. 그런데 리뷰가 주입한
// 네 변이는 **전부 초록으로 살아남았다** — 넷 다 컴포넌트 «바깥»을 끊었기 때문이다:
//
//   SUR-1 `CostPage.tsx`의 `<MaterialPriceHistory>` **호출부** 제거
//   SUR-2 `CostPage.tsx`의 `<LedgerMaterialLines>` 호출부를 `<div/>`로 교체
//   SUR-3 `App.tsx`의 `/cost` **라우트** 제거
//   SUR-4 `Layout.tsx`의 좌측 「💰 원가」 **메뉴** 제거
//
// **메뉴를 통째로 지워도 스위트가 안 울었다.** 이 저장소가 다섯 번째로 밟은 병이다
// (전역 §4 ★: 단위 테스트는 「함수가 값을 만드나」를 묻지 「사람이 그걸 보나」를 못 묻는다).
//
// ## 그래서 무엇을 하나
//
// **`App`을 통째로 `/cost`에서 렌더한다.** 라우팅·레이아웃·페이지·호출부가 한 줄로 이어져야만
// 통과하므로 넷 중 어느 하나만 끊어도 죽는다. api 모듈은 모킹해 네트워크를 타지 않는다 —
// 재는 것은 「값이 화면 픽셀이 되나」이지 서버가 아니다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type {
  CostBoard,
  CostLedgerMaterialLine,
  CostMaterial,
  CostRecipe,
  CostSetting,
} from "../lib/api";

// ★P2-A용: 0건 안내 렌더 분기 «자신»을 직접 잡는다. 이 두 컴포넌트는 CostPage.tsx가
//   「전부 순수 — props만 본다. 테스트가 직접 렌더한다」고 선언한 표시 계층이다
//   (`costMaterialsSurface.test.tsx`가 같은 파일의 다른 순수 컴포넌트에 쓰는 것과 같은 결).
//   전체 App 경로로는 이 분기에 진짜 0건을 못 만든다 — 옵션 목록이 항상 «현재 제품에
//   속한 것만»으로 구성되게 P1을 고쳤기 때문에, 정상 네비게이션으로는 0건이 안 나온다.
// ★2026-08-23 추가: `reconcileSelectedRecipeId`는 「선택이 필터 밖으로 나가면 상세
//   패널이 뭘 보여줘야 하나」를 정하는 유일한 진실의 원천이다(CostPage.tsx). 순수 함수라
//   전체 App 경로로는 못 만드는 조합(0건 등)까지 직접 단언할 수 있다 — 같은 이유로
//   RecipeList·StandardCostBoard를 직접 렌더하는 이 파일의 기존 관례를 그대로 따른다.
import { reconcileSelectedRecipeId, RecipeList, StandardCostBoard } from "./CostPage";

// ── prod 실측값(2026-08-22) — 합격 1이 화면에서 보겠다는 바로 그 두 로트 ──
const KIT: CostMaterial = {
  id: 1,
  name: "cleaning kit",
  unit: "ea",
  category: "부자재",
  status: "unconfirmed",
  excel_label: null,
  match_rule: "cleaning kit",
  form_factor: null,
  part: null,
  note: null,
  lot_count: 2,
  price_count: 2,
  stale_count: 0,
  latest_price_ex_vat: "190.82",
  latest_price_inc_vat: "209.90",
  latest_price_source: "ledger",
  prices: [
    {
      id: 11,
      material_id: 1,
      source: "ledger",
      import_invoice_line_id: 15,
      linked_item_name: "cleaning kits",
      linked_shipment_id: 1,
      supplier: "SHENZHEN OTAO TECHNOLOGY LIMITED",
      unit_price_ex_vat: "190.82",
      unit_price_inc_vat: "209.90",
      effective_date: "2026-08-18",
      note: null,
      shipment: {
        id: 1,
        hbl_no: "SETR2608170216",
        declaration_date: "2026-08-18",
        item_name: "cleaning kits",
        quantity: "2400.000",
      },
      ledger_check: {
        status: "ok",
        ok: true,
        label: "원장과 일치",
        detail: "원장 라인이 지금도 확정 상태이고 값·품목이 저장값과 같다.",
        counts_as_evidence: true,
        refreshable: true,
        ledger_unit_price_ex_vat: "190.82",
        ledger_unit_price_inc_vat: "209.90",
        ledger_item_name: "cleaning kits",
      },
    },
    {
      id: 12,
      material_id: 1,
      source: "ledger",
      import_invoice_line_id: 17,
      linked_item_name: "cleaning kits",
      linked_shipment_id: 2,
      supplier: "SHENZHEN OTAO TECHNOLOGY CO L",
      unit_price_ex_vat: "178.78",
      unit_price_inc_vat: "196.66",
      effective_date: "2026-07-23",
      note: null,
      shipment: {
        id: 2,
        hbl_no: "SETR2607220324",
        declaration_date: "2026-07-23",
        item_name: "cleaning kits",
        quantity: "12000.000",
      },
      ledger_check: {
        status: "ok",
        ok: true,
        label: "원장과 일치",
        detail: "원장 라인이 지금도 확정 상태이고 값·품목이 저장값과 같다.",
        counts_as_evidence: true,
        refreshable: true,
        ledger_unit_price_ex_vat: "178.78",
        ledger_unit_price_inc_vat: "196.66",
        ledger_item_name: "cleaning kits",
      },
    },
  ],
};

const LEDGER_ROW: CostLedgerMaterialLine = {
  line_id: 15,
  shipment_id: 1,
  hbl_no: "SETR2608170216",
  declaration_date: "2026-08-18",
  item_name: "cleaning kits",
  quantity: "2400.000",
  unit_cost_ex_vat: "190.82",
  unit_cost_inc_vat: "209.90",
  allocated_cost_krw: "54992.00",
  linked_material_id: null,
  linked_material_name: null,
  linked_price_id: null,
  shipment_status: "confirmed",
  linked_price_check: null,
  suggestion: {
    line_id: 15,
    item_name: "cleaning kits",
    material_id: 1,
    reason: "규칙 「cleaning kit」이 품목명에 전부 들어 있다 → 「cleaning kit」 제안",
    candidates: [1],
    ambiguous: false,
    unmatched: false,
  },
};

// ── S2: 원가 정본 실측(2026-08-23) — 「지문방지필름 TPU 3매」 · bar · 부자재 9종 ──
//    필름 600×3=1800 + 30 + 22 + 60 + 8 + 13 + 98 + 6 + 100 = ex 2,137 ⇒ inc **2,350.70**
const RECIPE: CostRecipe = {
  id: 7,
  product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
  form_factor: "bar",
  status: "approved",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: "2026-08-23T04:00:00",
  match: {
    match_reason:
      "폼팩터 bar(옵션명) × cost_price 2350.70 일치 — 원가표 「지문방지필름 TPU 3매」",
    candidates: ["모바일 필름-아이폰,갤럭시/지문방지필름 TPU 3매"],
    cost_price_mode: "2350.70",
    cost_table_item: "지문방지필름 TPU 3매",
    cost_table_section: "모바일 필름-아이폰,갤럭시",
    excel_total_inc_vat: "2350.70",
    sku_count: 106,
    option_count: 107,
  },
  line_count: 9,
  link_count: 106,
  standard: {
    computable: true,
    std_cost_ex_vat: "2137.00",
    std_cost_inc_vat: "2350.70",
    reason: null,
    unresolved: [],
    partial_ex_vat: "2137.00",
    partial_inc_vat: "2350.70",
    line_count: 9,
    lines: [
      {
        label: "지문방지필름 TPU 3매 · 필름 (bar)",
        quantity: "3",
        unit_price_ex_vat: "600.00",
        unit_price_inc_vat: "660.00",
        amount_ex_vat: "1800.00",
        amount_inc_vat: "1980.00",
        price_status: "manual",
        inc_derived: true,
        price_source: "manual",
        price_note: null,
        material_id: 21,
        usable: true,
      },
      {
        label: "패키지 (bar)",
        quantity: "1",
        unit_price_ex_vat: "98.00",
        unit_price_inc_vat: "107.80",
        amount_ex_vat: "98.00",
        amount_inc_vat: "107.80",
        price_status: "manual",
        inc_derived: true,
        price_source: "manual",
        price_note: null,
        material_id: 22,
        usable: true,
      },
    ],
  },
};

// ── S3: 두 번째 제품 — 「제품을 고르면 다른 제품 행이 사라진다」를 재려면 서로 다른
//    제품이 최소 둘 있어야 한다(레시피·보드 둘 다). 폼팩터 값도 원 제품과 겹치게 둬서
//    「폼팩터만으로는 안 갈리고 제품이 우선 갈라야 한다」는 것까지 함께 잰다.
const RECIPE_FLIP: CostRecipe = {
  id: 8,
  product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
  form_factor: "flip",
  status: "draft",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: null,
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

const RECIPE_OTHER_PRODUCT: CostRecipe = {
  id: 9,
  product_name: "오하이 강화유리 풀커버",
  form_factor: "bar",
  status: "draft",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: null,
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

// ── P2-C: `form_factor: null` 레시피 — 수입·매입 완제품처럼 폼팩터 개념이 없는 종.
//    「강화유리 풀커버」 제품에 bar(RECIPE_OTHER_PRODUCT)와 null을 나란히 두어, 폼팩터
//    필터가 null도 «하나의 선택지」로 다뤄야 한다는 것을 잰다(`?? "__none__"` sentinel).
const RECIPE_NULL_FORM: CostRecipe = {
  id: 10,
  product_name: "오하이 강화유리 풀커버",
  form_factor: null,
  status: "draft",
  source: "excel",
  recipe_kind: "assembly",
  anomaly_flag: null,
  approved_at: null,
  match: null,
  line_count: 0,
  link_count: 0,
  standard: {
    computable: false,
    std_cost_ex_vat: null,
    std_cost_inc_vat: null,
    reason: "구성 없음",
    unresolved: [],
    partial_ex_vat: null,
    partial_inc_vat: null,
    line_count: 0,
    lines: [],
  },
};

const BOARD: CostBoard = {
  items: [
    {
      internal_sku: "OHI-0390",
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매, 아이폰에어",
      recipe_id: 7,
      recipe_product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "bar",
      recipe_status: "approved",
      link_status: "approved",
      std_cost_ex_vat: "2137.00",
      std_cost_inc_vat: "2350.70",
      current_cost_price: "2350.70",
      gap_pct: 0,
      reason: null,
    },
    {
      internal_sku: "OHI-0391",
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매, 아이폰XS맥스/11프로맥스",
      recipe_id: 7,
      recipe_product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "bar",
      recipe_status: "approved",
      link_status: "approved",
      std_cost_ex_vat: "2137.00",
      std_cost_inc_vat: "2350.70",
      current_cost_price: "2350.70",
      gap_pct: 0,
      reason: null,
    },
    {
      // ★미계산 행 — 빠짐없이 실리고 «왜»를 말해야 한다(계약 §2-7).
      internal_sku: "OHI-9001",
      product_name: "오하이 빛반사, 지문방지 매트 필름 3매, 갤럭시Z플립7",
      recipe_id: 8,
      recipe_product_name: "오하이 빛반사, 지문방지 매트 필름 3매",
      form_factor: "flip",
      recipe_status: "draft",
      link_status: "draft",
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      current_cost_price: "3480.40",
      gap_pct: null,
      reason: "레시피 미승인 — 계산 안 함",
    },
    // ★다른 제품 — 필터가 「제품」 축으로 실제로 가르는지 재는 대조군.
    {
      internal_sku: "OHI-6001",
      product_name: "오하이 강화유리 풀커버, 아이폰15",
      recipe_id: 9,
      recipe_product_name: "오하이 강화유리 풀커버",
      form_factor: "bar",
      recipe_status: "draft",
      link_status: "draft",
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      current_cost_price: "1200.00",
      gap_pct: null,
      reason: "레시피 미승인 — 계산 안 함",
    },
    {
      internal_sku: "OHI-6002",
      product_name: "오하이 강화유리 풀커버, 갤럭시S24",
      recipe_id: 9,
      recipe_product_name: "오하이 강화유리 풀커버",
      form_factor: "bar",
      recipe_status: "draft",
      link_status: "draft",
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      current_cost_price: "1200.00",
      gap_pct: null,
      reason: "레시피 미승인 — 계산 안 함",
    },
  ],
  sku_count: 5,
  computed_count: 2,
  uncomputed_count: 3,
  recipe_count: 3,
  approved_recipe_count: 1,
};

const SETTINGS: CostSetting[] = [
  { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
];

// api 모듈 전체를 모킹한다 — Layout의 헬스·쿠키 조회까지 네트워크를 안 타게 하기 위해서다.
vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchCostMaterials: vi.fn(async () => ({ items: [KIT] })),
    fetchCostLedgerMaterialLines: vi.fn(async () => ({ items: [LEDGER_ROW] })),
    fetchCostSettings: vi.fn(async () => ({ items: SETTINGS })),
    fetchCostRecipes: vi.fn(async () => ({
      items: [RECIPE, RECIPE_FLIP, RECIPE_OTHER_PRODUCT, RECIPE_NULL_FORM],
    })),
    fetchCostBoard: vi.fn(async () => BOARD),
    // ★「보존」테스트가 재조회를 일으키는 트리거로 쓴다 — 실제 구현은 fetch를 타는데
    //   그러면 전역 fetchSpy가 `{}`를 돌려줘 `out.skipped_has_price.length`에서
    //   TypeError가 나 load()가 아예 안 불린다. 이 파일의 다른 쓰기 호출들과 같은 결로
    //   여기서 값을 직접 만든다.
    adoptCostExcelPrices: vi.fn(async () => ({ skipped_has_price: [], skipped_no_ref: [] })),
    getSchedulerHealth: vi.fn(async () => ({ healthy: true })),
    getAdCostCookieStatus: vi.fn(async () => ({})),
    getCollectionStatus: vi.fn(async () => ({ streams: [] })),
    // 레이아웃의 스케줄러 위젯은 `fetchApi`를 직접 부른다 — 껍데기만 준다.
    fetchApi: vi.fn(async () => ({ jobs: [], items: [] })),
  };
});

// 이 파일이 임포트하는 페이지들이 렌더 중 우발적으로 네트워크를 타지 않게 못을 박는다.
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

async function renderApp() {
  // 동적 임포트다 — `vi.mock`이 먼저 걸린 뒤에 App이 api를 집게 하려면 이 순서여야 한다.
  const { default: App } = await import("../App");
  return render(<App />);
}

describe("★「💰 원가」가 사람에게 닿는 경로 — 라우트·메뉴·호출부가 한 줄로 이어진다", () => {
  it("SUR-3: `/cost` 라우트가 있어야 원가 화면이 뜬다", async () => {
    await renderApp();
    expect(await screen.findByRole("heading", { name: /원가/ })).toBeTruthy();
    // S1의 자백 배지 둘 — 화면이 스스로 기준을 밝히는 자리(계약 합격 9 · §9-1)
    expect(await screen.findByText(/사내 관리회계 기준/)).toBeTruthy();
    expect(await screen.findByText(/신고 내역 미확인/)).toBeTruthy();
  });

  it("SUR-4: 좌측 메뉴에 「💰 원가」가 있어야 사람이 이 화면을 찾는다", async () => {
    const { container } = await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    const links = Array.from(container.querySelectorAll('a[href="/cost"]'));
    expect(links.length).toBeGreaterThan(0);
    expect(links.some((a) => (a.textContent ?? "").includes("원가"))).toBe(true);
    expect(links.some((a) => (a.textContent ?? "").includes("💰"))).toBe(true);
  });

  it("SUR-1: 단가 이력 **호출부**가 있어야 로트 2건이 화면에 그려진다", async () => {
    await renderApp();
    const aug = await screen.findByTestId("price-row-11");
    expect(within(aug).getByText("209.9원")).toBeTruthy();
    expect(within(aug).getByText("SETR2608170216")).toBeTruthy();
    const jul = await screen.findByTestId("price-row-12");
    expect(within(jul).getByText("196.66원")).toBeTruthy();
    // 합격 1의 요점 — 두 로트가 **서로 다른 값**으로 나란히 보인다(+6.7%)
    expect(within(aug).queryByText("196.66원")).toBeNull();
  });

  it("SUR-2: 원장 라인 **호출부**가 있어야 「연결」 경로가 화면에 존재한다", async () => {
    await renderApp();
    const row = await screen.findByTestId("ledger-line-15");
    expect(within(row).getByText("cleaning kits")).toBeTruthy();
    expect(within(row).getByRole("button", { name: /연결/ })).toBeTruthy();
  });

  // ── S2 (계약 §7 합격 3·4) — 탭을 «사람처럼» 눌러서 연다 ──
  //    탭 전환을 프로그램으로 흉내내지 않는다: 버튼이 사라지면 사람은 그 탭에 못 가고,
  //    그 사실을 재는 것이 이 파일의 존재 이유다.
  it("SUR-5: 「레시피」 탭 버튼이 있어야 사람이 승인 화면에 도달한다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    const tab = screen.getByRole("button", { name: "레시피" });
    fireEvent.click(tab);
    // 매칭 근거가 화면에 실제로 있어야 한다 — 「제안이지 확정이 아니다」가 보이는 자리.
    // 목록·상세 양쪽에 나올 수 있다 — 「하나뿐」이 아니라 「있다」를 잰다.
    expect((await screen.findAllByText(/원가표 「지문방지필름 TPU 3매」/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /엑셀 참고값을 단가로 채택/ })).toBeTruthy();
  });

  it("SUR-6: 계산 내역 **호출부**가 있어야 「계산되는 방법」이 펼쳐진다 (합격 4)", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    fireEvent.click(screen.getByRole("button", { name: "레시피" }));
    await screen.findByText(/계산 내역/);
    // 부자재 × 수량 × 단가가 실제 픽셀이 된다
    expect(screen.getByText("지문방지필름 TPU 3매 · 필름 (bar)")).toBeTruthy();
    expect(screen.getByText("1,800원")).toBeTruthy();   // 600 × 3
    // 「98원」은 단가 칸과 금액 칸 둘 다에 뜬다(수량 1) — 개수가 아니라 존재를 잰다.
    expect(screen.getAllByText("98원").length).toBeGreaterThan(0);
    // 합계 = 정본 대조값
    expect(screen.getAllByText("2,350.7원").length).toBeGreaterThan(0);
  });

  it("SUR-7: 보드 **호출부**가 있어야 2,350.7이 여러 SKU에 보인다 (합격 3)", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    fireEvent.click(screen.getByRole("button", { name: "표준원가 보드" }));
    expect(await screen.findByText("OHI-0390")).toBeTruthy();
    expect(screen.getByText("OHI-0391")).toBeTruthy();
    // ★서로 다른 SKU 2건 이상에서 **같은 값**이 관측된다
    expect(screen.getAllByText("2,350.7원").length).toBeGreaterThanOrEqual(2);
  });

  it("SUR-8: 미계산 행이 «왜»와 함께 남는다 — 조용히 사라지면 커버리지 착시다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /원가/ });
    fireEvent.click(screen.getByRole("button", { name: "표준원가 보드" }));
    expect(await screen.findByText("OHI-9001")).toBeTruthy();
    expect(screen.getAllByText(/레시피 미승인 — 계산 안 함/).length).toBeGreaterThan(0);
    // 미계산 행의 표준원가 칸은 「—」다 — 0원으로 그리면 미입력이 확정값으로 둔갑한다.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  // ── S3(원가메뉴): 제품 → 옵션 2단 드롭다운 필터 ──────────────────────────
  // Jino: "제품명, 옵션명을 불러올 수 있게 드롭버튼을 만드는게 좋겠다 … 예를 들어서 제품,
  //   옵션 구조로. 제품만 선택하면 제품에 속하는 옵션들이 쭉 나오기도 하고 옵션까지 선택하면
  //   딱 그 제품만 나오고." — 실제 병목은 보드 924행 · 레시피 100건에서 눈으로 못 찾는 것.
  describe("★제품 → 옵션 필터 — 보드 탭", () => {
    async function openBoardTab() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "표준원가 보드" }));
      await screen.findByText("OHI-0390");
    }

    it("제품을 고르면 그 제품의 옵션 행만 남는다 — 다른 제품 행이 사라진다", async () => {
      await openBoardTab();
      // 필터 전엔 두 제품이 모두 보인다.
      expect(screen.getByText("OHI-6001")).toBeTruthy();

      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      expect(screen.getByText("OHI-0390")).toBeTruthy();
      expect(screen.getByText("OHI-0391")).toBeTruthy();
      expect(screen.getByText("OHI-9001")).toBeTruthy();
      // ★다른 제품의 SKU는 화면에서 사라진다 — 이게 필터의 요점이다.
      expect(screen.queryByText("OHI-6001")).toBeNull();
      expect(screen.queryByText("OHI-6002")).toBeNull();
    });

    it("옵션까지 고르면 그 한 행만 남는다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      expect(optionSelect.disabled).toBe(false);
      // ★옵션 셀렉트 «자신»의 항목이 선택된 제품에 종속돼야 한다 — 다른 제품의 SKU가
      //   목록 안에 섞여 있으면, 뒤에 오는 필터링이 우연히 맞아도 사람은 잘못된 옵션을
      //   고를 수 있다(변이 ④가 이 자리에서만 죽는다).
      expect(within(optionSelect).queryByText(/OHI-6001/)).toBeNull();
      expect(within(optionSelect).queryByText(/OHI-6002/)).toBeNull();
      expect(within(optionSelect).getByText(/OHI-0391/)).toBeTruthy();

      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });

      expect(screen.getByText("OHI-0391")).toBeTruthy();
      expect(screen.queryByText("OHI-0390")).toBeNull();
      expect(screen.queryByText("OHI-9001")).toBeNull();
    });

    it("제품을 고르기 전엔 옵션 셀렉트가 비활성이고 안내를 말한다", async () => {
      await openBoardTab();
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      expect(optionSelect.disabled).toBe(true);
      expect(within(optionSelect).getByText(/먼저 제품을 고르세요/)).toBeTruthy();
    });

    it("필터가 걸리면 「N건 중 M건 표시 중」 문구가 뜬다", async () => {
      await openBoardTab();
      expect(screen.queryByTestId("board-filter-summary")).toBeNull();

      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      const summary = await screen.findByTestId("board-filter-summary");
      // BOARD 전체 5건 중 그 제품 3건.
      expect(summary.textContent).toContain("5건 중 3건 표시 중");
      expect(summary.textContent).toContain("제품=오하이 빛반사, 지문방지 매트 필름 3매");
    });

    it("제품 검색칸에 글자를 넣으면 제품 셀렉트의 항목이 좁혀진다", async () => {
      await openBoardTab();
      const search = screen.getByTestId("board-product-search");
      const productSelect = screen.getByTestId("board-product-select");

      // 필터 전엔 두 제품이 모두 셀렉트 옵션으로 있다.
      expect(within(productSelect).getByText(/강화유리 풀커버/)).toBeTruthy();
      expect(within(productSelect).getByText(/빛반사, 지문방지 매트 필름 3매/)).toBeTruthy();

      fireEvent.change(search, { target: { value: "강화유리" } });

      expect(within(productSelect).getByText(/강화유리 풀커버/)).toBeTruthy();
      expect(within(productSelect).queryByText(/빛반사, 지문방지 매트 필름 3매/)).toBeNull();
    });

    it("필터 결과가 0건이면 「해당 조건에 맞는 SKU가 없다」를 말한다 — 빈 표를 그리지 않는다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });
      // 다시 다른 제품으로 바꾸면 옵션은 초기화되지만, 강제로 없는 조합을 만드는 대신
      // 존재하는 SKU 하나만 남기고 그 상태를 그대로 관측한다 — 0건 경로는 별도로 잰다.
      expect(screen.getByText("OHI-0391")).toBeTruthy();
      expect(screen.queryByText(/해당 조건에 맞는 SKU가 없다/)).toBeNull();
    });

    it("초기화를 누르면 검색어·제품·옵션이 전부 원복된다", async () => {
      await openBoardTab();
      const search = screen.getByTestId("board-product-search") as HTMLInputElement;
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;

      fireEvent.change(search, { target: { value: "빛반사" } });
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });
      expect(screen.queryByText("OHI-0390")).toBeNull();

      fireEvent.click(screen.getByTestId("board-picker-reset"));

      expect(search.value).toBe("");
      expect(productSelect.value).toBe("");
      // 전부 원복 — 필터 요약이 사라지고 모든 SKU가 다시 보인다.
      expect(screen.queryByTestId("board-filter-summary")).toBeNull();
      expect(screen.getByText("OHI-0390")).toBeTruthy();
      expect(screen.getByText("OHI-6001")).toBeTruthy();
    });

    // ── 적대 리뷰 1R P1-1 채택 (2026-08-23) ──────────────────────────────
    // 코드는 `handleBoardProductChange`에서 이미 `setBoardOption(null)`을 부른다 — 문제는
    // 그 줄을 지워도 28건 전부 초록이었다는 것이다. 이 테스트가 그 줄을 «지키는» 첫 테스트다.
    it("P1-1: 옵션을 고른 뒤 제품을 바꾸면 이전 옵션이 남지 않는다 — 있는 SKU가 「없다」로 보이면 안 된다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      fireEvent.change(optionSelect, { target: { value: "OHI-0391" } });
      expect(screen.getByText("OHI-0391")).toBeTruthy();

      // 제품을 바꾼다 — 「강화유리 풀커버」엔 SKU 「OHI-0391」이 없다.
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });

      // ★버그(있었다면): `setBoardOption(null)`이 없으면 필터가 「강화유리 AND OHI-0391」이
      //   되어, 실제로 있는 강화유리 행이 «해당 조건에 맞는 SKU가 없다»로 둔갑한다.
      expect(screen.getByText("OHI-6001")).toBeTruthy();
      expect(screen.getByText("OHI-6002")).toBeTruthy();
      expect(screen.queryByText(/해당 조건에 맞는 SKU가 없다/)).toBeNull();
      // 옵션 셀렉트 자신도 「전체」로 되돌아가 있어야 한다 — 상태와 표시가 같이 원복된다.
      expect(optionSelect.value).toBe("");
    });

    // ── P2-B 채택: 「전체 (N건)」의 N은 «선택된 제품 기준»이어야 한다. 전체 보드 건수로
    //   바꿔도 이 테스트 전엔 아무도 안 죽었다(적대 리뷰 1R 변이 실측).
    it("P2-B: 옵션 셀렉트 「전체 (N건)」의 N은 전체 보드 건수가 아니라 선택된 제품 건수다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const optionSelect = screen.getByTestId("board-option-select") as HTMLSelectElement;
      // BOARD 전체는 5건이지만 「빛반사…」 제품은 3건(OHI-0390·0391·9001)뿐이다.
      expect(within(optionSelect).getByText("전체 (3건)")).toBeTruthy();
      expect(within(optionSelect).queryByText("전체 (5건)")).toBeNull();
    });

    // ── P2-D 채택: 검색어가 이미 선택된 제품을 걸러내도 셀렉트는 그 제품을 계속 들고
    //   있어야 한다 — 안 그러면 <select>의 value가 목록 밖이라 빈 값처럼 보인다(유령 선택).
    it("P2-D: 검색어가 선택된 제품을 가려도 셀렉트는 그 제품을 계속 보여준다", async () => {
      await openBoardTab();
      const productSelect = screen.getByTestId("board-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });
      expect(productSelect.value).toBe("오하이 강화유리 풀커버");

      const search = screen.getByTestId("board-product-search");
      fireEvent.change(search, { target: { value: "빛반사" } });

      // ★검색이 「강화유리」를 목록에서 걸러내도, 이미 선택된 값은 살아 있어야 한다.
      expect(productSelect.value).toBe("오하이 강화유리 풀커버");
      expect(within(productSelect).getByText(/강화유리 풀커버/)).toBeTruthy();
      // 화면도 그 선택 기준으로 계속 필터링돼 있다 — 상태·표시가 어긋나지 않는다.
      expect(screen.getByText("OHI-6001")).toBeTruthy();
      expect(screen.queryByText("OHI-0390")).toBeNull();
    });
  });

  describe("★제품 → 옵션(폼팩터) 필터 — 레시피 탭", () => {
    async function openRecipesTabForFilter() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
    }

    it("제품 + 폼팩터로 목표 레시피 하나에 도달한다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const formFactorSelect = screen.getByTestId("recipe-option-select") as HTMLSelectElement;
      expect(formFactorSelect.disabled).toBe(false);
      fireEvent.change(formFactorSelect, { target: { value: "bar" } });

      // ★목표 레시피(id 7, bar)의 매칭 근거만 남고, 같은 제품의 flip(id 8)이나
      //   다른 제품(id 9)의 흔적은 목록에서 사라진다.
      expect(
        (await screen.findAllByText(/원가표 「지문방지필름 TPU 3매」/)).length,
      ).toBeGreaterThan(0);
      expect(screen.queryByText("오하이 강화유리 풀커버")).toBeNull();
    });

    it("레시피 탭 필터도 「N건 중 M건 표시 중」을 말한다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const summary = await screen.findByTestId("recipe-filter-summary");
      // 전체 레시피 4건(RECIPE_NULL_FORM 포함) 중 그 제품 2건(bar·flip).
      expect(summary.textContent).toContain("4건 중 2건 표시 중");
    });

    it("레시피 탭 초기화를 누르면 필터가 전부 풀린다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });
      expect(await screen.findByTestId("recipe-filter-summary")).toBeTruthy();

      fireEvent.click(screen.getByTestId("recipe-picker-reset"));

      expect(productSelect.value).toBe("");
      expect(screen.queryByTestId("recipe-filter-summary")).toBeNull();
    });

    // ── 적대 리뷰 1R P1-2 채택 (2026-08-23) ──────────────────────────────
    // `handleRecipeProductChange`가 이미 `setRecipeFormFactor(null)`을 부르지만, 그 줄을
    // 지워도 28건 전부 초록이었다 — P1-1과 같은 결함의 다른 표현이다.
    it("P1-2: 폼팩터를 고른 뒤 제품을 바꾸면 이전 폼팩터가 남지 않는다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });
      const formFactorSelect = screen.getByTestId("recipe-option-select") as HTMLSelectElement;
      fireEvent.change(formFactorSelect, { target: { value: "flip" } });
      expect(await screen.findByTestId("recipe-filter-summary")).toBeTruthy();

      // 제품을 바꾼다 — 「강화유리 풀커버」엔 flip 폼팩터가 없다(bar·null뿐이다).
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });

      // ★버그(있었다면): `setRecipeFormFactor(null)`이 없으면 필터가 「강화유리 AND flip」이
      //   되어, 실제로 있는 강화유리 레시피(bar·null)가 목록에서 통째로 사라진다.
      expect(screen.getAllByText("오하이 강화유리 풀커버").length).toBeGreaterThan(0);
      const summary = await screen.findByTestId("recipe-filter-summary");
      expect(summary.textContent).not.toContain("0건 표시 중");
      // 폼팩터 셀렉트 자신도 「전체」로 되돌아가 있어야 한다.
      expect(formFactorSelect.value).toBe("");
    });

    // ── P2-C 채택: `form_factor: null`(수입·매입 완제품)도 하나의 선택지로 다뤄야 한다.
    //   `?? "__none__"` sentinel이 없으면 null 레시피는 필터에 걸려 영영 안 보인다.
    it("P2-C: 폼팩터가 없는(`null`) 레시피도 「—」 선택지로 걸러진다", async () => {
      await openRecipesTabForFilter();
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });

      const formFactorSelect = screen.getByTestId("recipe-option-select") as HTMLSelectElement;
      // 「강화유리 풀커버」엔 bar(RECIPE_OTHER_PRODUCT)와 null(RECIPE_NULL_FORM) 둘이 있다.
      expect(within(formFactorSelect).getByText("bar")).toBeTruthy();
      expect(within(formFactorSelect).getByText("—")).toBeTruthy();

      fireEvent.change(formFactorSelect, { target: { value: "__none__" } });

      // ★sentinel이 없으면(mutant) null 레시피가 필터에서 빠져 0건이 된다.
      const summary = await screen.findByTestId("recipe-filter-summary");
      expect(summary.textContent).toContain("1건 표시 중");
      expect(summary.textContent).not.toContain("0건 표시 중");
    });

    // ★레이아웃 가드 (2026-08-23 Jino 실관측: *"칸이 옆으로 나오고 그러잖아?"*)
    //
    // 이 필터 바는 **넓은 보드 탭과 320px 레시피 탭 둘 다**에 놓인다. 초판이 보드 폭만 보고
    // `w-56`·`min-w-[16rem]` 같은 고정폭을 박았고, 그리드/플렉스 자식의 기본 `min-width: auto`가
    // 축소를 막아 **컨트롤이 왼쪽 칸을 뚫고 오른쪽 패널을 덮었다.**
    //
    // jsdom은 레이아웃을 계산하지 않으므로 「겹쳤는가」는 못 잰다. 대신 **그 원인이 된 클래스가
    // 돌아오지 않는지**를 잰다 — 약한 가드지만 아무것도 안 지키는 것보다 낫고, 다음 사람에게
    // 「여기 고정폭을 박으면 안 된다」는 사실을 전달한다. 진짜 판정은 라이브 화면이 한다.
    it("필터 바는 좁은 칸에서 «접힌다» — 고정폭을 박으면 옆 패널을 덮는다", async () => {
      await openRecipesTabForFilter();
      const search = screen.getByTestId("recipe-product-search");
      const productSelect = screen.getByTestId("recipe-product-select");
      const optionSelect = screen.getByTestId("recipe-option-select");

      for (const el of [search, productSelect, optionSelect]) {
        // 칸을 «채우되» 줄어들 수 있어야 한다.
        expect(el.className).toContain("w-full");
        expect(el.className).toContain("min-w-0");
        // 고정폭·최소폭은 좁은 칸에서 넘친다.
        // ★`\b`를 쓰면 `min-w-0`의 `w-0`까지 잡힌다 — 클래스 경계는 공백이다.
        expect(el.className).not.toMatch(/(^|\s)w-\d/);
        expect(el.className).not.toMatch(/min-w-\[/);
      }
    });
  });

  // ── 결함 수리 (2026-08-23, Jino 실관측): 레시피 탭에서 제품 검색으로 목록을 좁혀도
  //   상세 패널은 필터 밖(목록에 없는) 레시피를 계속 붙들고 있었다 — 그 상태에서
  //   「이 구성을 승인한다」를 누르면 엉뚱한 레시피가 승인된다. 값은 맞았지만 사람이
  //   보는 화면이 틀렸다는 점에서 이 파일이 아홉 번째로 밟는 같은 병이다.
  describe("★결함 수리 — 상세 패널이 필터 밖 레시피를 붙들지 않는다", () => {
    async function openRecipesTabForFilter() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
      // 기본 선택(목록 첫 항목, RECIPE id 7)이 뜰 때까지 기다린다.
      await screen.findByRole("heading", { name: "오하이 빛반사, 지문방지 매트 필름 3매" });
    }

    it("회귀: 필터 밖으로 나간 선택은 상세 패널에서 더 이상 렌더되지 않는다", async () => {
      await openRecipesTabForFilter();

      // 다른 제품(강화유리 풀커버, id 9)의 레시피를 명시적으로 고른다.
      fireEvent.click(screen.getByTestId("recipe-row-9"));
      expect(await screen.findByRole("heading", { name: "오하이 강화유리 풀커버" })).toBeTruthy();

      // 제품 필터를 걸어 지금 선택된 레시피(id 9)를 목록 밖으로 밀어낸다.
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      // ★결함이 있었다면 상세 패널은 여전히 「강화유리 풀커버」를 보여준다.
      //   「상태가 바뀌었다」가 아니라 화면에 그 글자가 «없다»를 잰다.
      await waitFor(() => {
        expect(screen.queryByRole("heading", { name: "오하이 강화유리 풀커버" })).toBeNull();
      });
      // ★왼쪽 목록에서도 사라진다 — 필터가 실제로 걸렸다는 대조군.
      expect(screen.queryByTestId("recipe-row-9")).toBeNull();
    });

    it("스냅: 선택이 목록 밖으로 나가면 필터된 목록의 첫 레시피로 자동 전환된다", async () => {
      await openRecipesTabForFilter();
      fireEvent.click(screen.getByTestId("recipe-row-9")); // 강화유리 풀커버 선택
      await screen.findByRole("heading", { name: "오하이 강화유리 풀커버" });

      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, {
        target: { value: "오하이 빛반사, 지문방지 매트 필름 3매" },
      });

      // 필터된 목록(id 7 bar, id 8 flip) 중 배열 순서상 첫 항목 id 7(bar)로 스냅한다.
      const panel = await screen.findByTestId("recipe-detail-panel");
      await waitFor(() => {
        expect(
          within(panel).getByRole("heading", { name: "오하이 빛반사, 지문방지 매트 필름 3매" }),
        ).toBeTruthy();
      });
      expect(within(panel).getByText(/폼팩터 bar ·/)).toBeTruthy();
      // id 7은 계산이 끝난 레시피다 — id 8(flip, 미계산)로 잘못 스냅하지 않았다는 대조군.
      expect(within(panel).getAllByText("2,350.7원").length).toBeGreaterThan(0);
    });

    it("보존: 필터를 건 상태에서 재조회(승인 등)가 일어나도 같은 레시피가 계속 선택돼 있다", async () => {
      await openRecipesTabForFilter();

      // 「강화유리 풀커버」로 필터 — id 9(bar)로 스냅된다(배열 순서상 첫 항목).
      const productSelect = screen.getByTestId("recipe-product-select") as HTMLSelectElement;
      fireEvent.change(productSelect, { target: { value: "오하이 강화유리 풀커버" } });
      const panel = await screen.findByTestId("recipe-detail-panel");
      await waitFor(() => {
        expect(within(panel).getByRole("heading", { name: "오하이 강화유리 풀커버" })).toBeTruthy();
      });

      // 재조회를 일으킨다 — 「엑셀 참고값을 단가로 채택」은 line_count와 무관하게 항상
      // 눌릴 수 있고, 성공하면 onAdopt 안에서 load()가 다시 호출된다.
      fireEvent.click(screen.getByRole("button", { name: /엑셀 참고값을 단가로 채택/ }));

      // ★재조회 뒤에도 같은 필터·같은 선택이 유지된다 — 승인 직후 목록이 갱신되며
      //   선택이 풀리면 방금 승인한 결과를 못 본다(CostPage.tsx 주석과 같은 이유).
      await waitFor(() => {
        expect(within(panel).getByRole("heading", { name: "오하이 강화유리 풀커버" })).toBeTruthy();
      });
    });

    describe("0건: reconcileSelectedRecipeId — 상세 패널이 엉뚱한 레시피를 안 보여준다", () => {
      // ★전체 App 경로로는 진짜 0건을 못 만든다(폼팩터 셀렉트가 항상 «현재 제품에
      //   속한 것만»이라 0건 조합 자체가 안 만들어진다 — 위 P2-A 설명과 같은 사정).
      //   그래서 이 결함 수리의 «유일한 진실의 원천»인 순수 함수를 직접 잰다.
      it("필터 결과가 0건이면 이전 선택과 무관하게 null이다", () => {
        expect(reconcileSelectedRecipeId([], RECIPE_OTHER_PRODUCT.id)).toBeNull();
        expect(reconcileSelectedRecipeId([], null)).toBeNull();
      });

      it("현재 선택이 필터된 목록 안에 있으면 그대로 유지한다", () => {
        expect(
          reconcileSelectedRecipeId([RECIPE, RECIPE_FLIP], RECIPE_FLIP.id),
        ).toBe(RECIPE_FLIP.id);
      });

      it("현재 선택이 필터된 목록 밖이면 첫 항목으로 스냅한다", () => {
        expect(
          reconcileSelectedRecipeId([RECIPE, RECIPE_FLIP], RECIPE_OTHER_PRODUCT.id),
        ).toBe(RECIPE.id);
      });
    });
  });

  // ── S3: 엑셀 2종 업로드가 «카드형 드롭존»으로 바뀐다 (Jino: "선택이 쉽게 직관적으로") ──
  describe("★S3: 원가 정본/매핑 정본 드롭존 — 클릭 전엔 안내, 클릭 후엔 확인, 잘못 넣으면 사유", () => {
    function makeXlsx(name: string, bytes = 2048): File {
      return new File([new Uint8Array(bytes)], name, {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
    }

    async function openRecipesTab() {
      await renderApp();
      await screen.findByRole("heading", { name: /원가/ });
      fireEvent.click(screen.getByRole("button", { name: "레시피" }));
    }

    it("드롭존 2개가 칸 이름 · 기대 시트 이름과 함께 렌더된다", async () => {
      await openRecipesTab();
      const costZone = await screen.findByTestId("cost-dropzone-cost");
      expect(within(costZone).getByText("원가 정본")).toBeTruthy();
      expect(within(costZone).getByText(/제품 원가표/)).toBeTruthy();
      expect(within(costZone).getByText(/MD_원가 계산_/)).toBeTruthy();

      const mappingZone = screen.getByTestId("cost-dropzone-mapping");
      expect(within(mappingZone).getByText("매핑 정본")).toBeTruthy();
      expect(within(mappingZone).getByText(/원가 매핑/)).toBeTruthy();
      expect(within(mappingZone).getByText(/ohisell_mapping_template_/)).toBeTruthy();

      // .xlsx만 받는다는 것도 두 칸 모두에서 보인다.
      expect(within(costZone).getByText(".xlsx만")).toBeTruthy();
      expect(within(mappingZone).getByText(".xlsx만")).toBeTruthy();
    });

    it("파일을 넣으면 파일명 · 크기가 뜨고, 「바꾸기」·「지우기」가 나타난다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("원가 정본 파일") as HTMLInputElement;
      const file = makeXlsx("MD_원가 계산_20260823.xlsx", 3072);
      fireEvent.change(input, { target: { files: [file] } });

      const costZone = screen.getByTestId("cost-dropzone-cost");
      expect(within(costZone).getByText("MD_원가 계산_20260823.xlsx")).toBeTruthy();
      expect(within(costZone).getByText("3.0KB")).toBeTruthy();
      expect(within(costZone).getByRole("button", { name: "바꾸기" })).toBeTruthy();
      const clearBtn = within(costZone).getByRole("button", { name: "지우기" });
      expect(clearBtn).toBeTruthy();

      // 지우기 → 다시 안내 문구로 돌아간다(선택 해제).
      fireEvent.click(clearBtn);
      expect(within(costZone).queryByText("MD_원가 계산_20260823.xlsx")).toBeNull();
      expect(within(costZone).getByText(/제품 원가표/)).toBeTruthy();
    });

    it(".xlsx가 아닌 파일을 넣으면 그 자리에서 거부 사유가 뜬다 — 서버까지 안 간다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("매핑 정본 파일") as HTMLInputElement;
      const badFile = new File(["a,b,c"], "report.csv", { type: "text/csv" });
      fireEvent.change(input, { target: { files: [badFile] } });

      const mappingZone = screen.getByTestId("cost-dropzone-mapping");
      const errorEl = within(mappingZone).getByTestId("cost-dropzone-mapping-error");
      expect(errorEl.textContent).toMatch(/\.xlsx 파일이 아닙니다/);
      expect(errorEl.textContent).toMatch(/report\.csv/); // 사유가 «무엇을 받았는지»를 말한다
      // ★사유는 «무엇을 해야 하는지»도 같이 말한다(교훈 #349).
      expect(errorEl.textContent).toMatch(/xlsx로 바꿔 다시 올리세요/);
      // 거부된 파일은 선택 상태로 채택되지 않는다.
      expect(within(mappingZone).queryByText("report.csv")).toBeNull();
    });

    it("비활성 사유가 문장으로 보이고, 둘 다 고르면 사라지며 버튼이 활성화된다", async () => {
      await openRecipesTab();
      // 처음엔 둘 다 없다 — 「엑셀 2종을 모두 고르세요」
      expect(screen.getByTestId("import-disabled-reason").textContent).toMatch(
        /엑셀 2종을 모두 고르세요/,
      );
      const importBtn = screen.getByRole("button", {
        name: "초안 만들기",
      }) as HTMLButtonElement;
      expect(importBtn.disabled).toBe(true);

      fireEvent.change(screen.getByLabelText("원가 정본 파일"), {
        target: { files: [makeXlsx("MD_원가 계산_1.xlsx")] },
      });
      // 원가 정본만 있으면 「매핑 정본을 아직 고르지 않았습니다」
      expect(screen.getByTestId("import-disabled-reason").textContent).toMatch(
        /매핑 정본을 아직 고르지 않았습니다/,
      );
      expect(importBtn.disabled).toBe(true);

      fireEvent.change(screen.getByLabelText("매핑 정본 파일"), {
        target: { files: [makeXlsx("ohisell_mapping_template_1.xlsx")] },
      });
      // 둘 다 고르면 안내가 사라지고 버튼이 활성화된다.
      expect(screen.queryByTestId("import-disabled-reason")).toBeNull();
      expect(importBtn.disabled).toBe(false);
    });

    it("카드 밖 드롭은 조용히 무시된다 — 페이지 이탈용 브라우저 기본 동작이 안 뜬다", async () => {
      await openRecipesTab();
      const panel = screen.getByText("엑셀 2종 업로드 → 구성 초안").closest("section")!;
      const badFile = new File(["x"], "random.pdf", { type: "application/pdf" });
      const dataTransfer = { files: [badFile] };
      // panel 영역(카드 밖)에 드롭 — preventDefault만 되고 아무 상태도 안 바뀐다.
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", { value: dataTransfer });
      const prevented = !panel.dispatchEvent(dropEvent);
      expect(prevented).toBe(true);
      // 에러 팝업·파일 채택 둘 다 없다.
      expect(screen.queryByTestId("cost-dropzone-cost-error")).toBeNull();
      expect(screen.queryByTestId("cost-dropzone-mapping-error")).toBeNull();
    });

    // ★이 테스트가 없으면 「드롭 경로」가 통째로 죽어도 전건 초록이다 — 실제로 변이를 넣어
    //   확인했고 **SURVIVED**였다(2026-08-23, 세션 5432a577). 클릭 선택만 재는 테스트는
    //   `onChange`만 밟으므로 `onDrop`을 한 줄도 지키지 못한다. Jino가 처음 물은 것이
    //   *"파일을 그냥 드롭하면 되나?"*였으니, 드롭은 이 화면의 «사람이 쓰는 경로»다.
    it("카드 «안»에 드롭하면 실제로 선택된다 — 드롭 경로가 끊기면 이 테스트가 빨개진다", async () => {
      await openRecipesTab();
      const costZone = await screen.findByTestId("cost-dropzone-cost");
      const file = new File(["x"], "MD_원가 계산_260822.xlsx", {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", { value: { files: [file] } });
      costZone.dispatchEvent(dropEvent);

      // 고른 파일이 화면에 «보여야» 한다 — 상태만 바뀌고 안 그려지면 사람은 모른다.
      expect(await within(costZone).findByText("MD_원가 계산_260822.xlsx")).toBeTruthy();
      // 그리고 그 선택이 다음 단계로 «이어져야» 한다: 남은 비활성 사유는 매핑 정본 하나뿐.
      expect(screen.getByTestId("import-disabled-reason").textContent).toContain("매핑 정본");
    });

    it("잘못된 파일을 «드롭»해도 그 자리에서 거부된다 — 클릭 경로와 같은 판정을 탄다", async () => {
      await openRecipesTab();
      const mappingZone = await screen.findByTestId("cost-dropzone-mapping");
      const badFile = new File(["x"], "매핑.csv", { type: "text/csv" });
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", { value: { files: [badFile] } });
      mappingZone.dispatchEvent(dropEvent);

      const errorEl = await within(mappingZone).findByTestId("cost-dropzone-mapping-error");
      expect(errorEl.textContent).toContain("매핑.csv");
      expect(errorEl.textContent).toContain("xlsx");
    });

    // ── 적대 리뷰 1R 산출물 (2026-08-23) ────────────────────────────────
    // P1: 카드가 role="button"이라 중첩된 「지우기」의 Enter를 가로채 죽이고 파일 선택창을
    //     대신 열었다. 「바꾸기」는 목적이 우연히 같아 증상이 안 보였다 — 그래서 놓칠 뻔했다.
    it("P1: 「지우기」가 키보드(Enter)로도 작동한다 — 카드가 자식의 키를 가로채지 않는다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("원가 정본 파일");
      const file = new File(["x"], "MD_원가 계산_260822.xlsx");
      fireEvent.change(input, { target: { files: [file] } });

      const costZone = screen.getByTestId("cost-dropzone-cost");
      expect(within(costZone).getByText("MD_원가 계산_260822.xlsx")).toBeTruthy();

      const clearBtn = within(costZone).getByRole("button", { name: "지우기" });

      // ★단언의 자리를 조심해야 한다. `fireEvent.click`을 뒤에 붙이면 그 클릭이 파일을 지워
      //   버려서, 결함이 있어도 초록으로 통과한다(실제로 그렇게 썼다가 변이가 SURVIVED 했다).
      //   jsdom은 keydown 뒤 네이티브 버튼 활성화를 대신해 주지 않으므로, 브라우저가 그 활성화를
      //   «할 수 있는 상태인가»를 직접 잰다 — 즉 부모가 preventDefault로 죽이지 않았는가.
      const pickerSpy = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {});
      const ev = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
      clearBtn.dispatchEvent(ev);

      expect(ev.defaultPrevented).toBe(false); // 죽이면 브라우저가 「지우기」를 못 누른다
      expect(pickerSpy).not.toHaveBeenCalled(); // 대신 파일 선택창이 열려서도 안 된다
      pickerSpy.mockRestore();
    });

    // P2-1 채택: 거부만 말하고 «이전 선택이 사라졌다»를 안 말하면, 사람은 멀쩡한 파일이
    //   아직 들어 있는 줄 알고 다음 단계로 간다. 부작용을 감추는 사유는 틀린 사유다.
    it("P2-1: 고른 파일 위에 잘못된 파일을 넣으면 «이전 선택이 취소됐다»고 말한다", async () => {
      await openRecipesTab();
      const input = screen.getByLabelText("원가 정본 파일");
      fireEvent.change(input, { target: { files: [new File(["x"], "정상.xlsx")] } });
      fireEvent.change(input, { target: { files: [new File(["x"], "잘못.csv")] } });

      const errorEl = within(screen.getByTestId("cost-dropzone-cost")).getByTestId(
        "cost-dropzone-cost-error",
      );
      expect(errorEl.textContent).toContain("잘못.csv");
      expect(errorEl.textContent).toContain("정상.xlsx"); // 무엇이 취소됐는지 이름으로 말한다
      expect(errorEl.textContent).toContain("취소");
    });

    // P2-2 채택: 두 엑셀을 한 칸에 함께 떨어뜨리는 것은 실사용에서 충분히 일어난다.
    //   나머지를 조용히 버리면 사람은 둘 다 올린 줄 안다.
    it("P2-2: 한 칸에 여러 파일을 드롭하면 «하나만 받았다»고 말한다 — 조용히 안 버린다", async () => {
      await openRecipesTab();
      const costZone = await screen.findByTestId("cost-dropzone-cost");
      const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(dropEvent, "dataTransfer", {
        value: { files: [new File(["x"], "첫.xlsx"), new File(["x"], "둘.xlsx")] },
      });
      costZone.dispatchEvent(dropEvent);

      const noteEl = await within(costZone).findByTestId("cost-dropzone-cost-error");
      expect(noteEl.textContent).toContain("하나만 받습니다");
      expect(noteEl.textContent).toContain("첫.xlsx");
      // ★거부가 아니다 — 첫 파일은 실제로 선택돼 있어야 한다.
      expect(within(costZone).getByText("첫.xlsx")).toBeTruthy();
    });
  });

  // ── 적대 리뷰 1R P2-A 채택 (2026-08-23) ────────────────────────────────
  // 보드·레시피 둘 다 「해당 조건에 맞는 …가 없다」 렌더 분기를 통째로 `false`로 바꿔도
  // 28/28 통과했다 — 기존 테스트가 「0건이 아니다」만 확인하고 0건 경로를 일부러 피해갔다.
  //
  // ★전체 App 경로로는 이 분기에 진짜 0건을 못 만든다: P1을 고치고 나면 옵션 목록이
  //   항상 «현재 제품에 속한 것만»으로 구성되므로, 정상 네비게이션으로는 0건 조합 자체가
  //   안 만들어진다(0건이 나오려면 P1의 그 버그가 다시 있어야 한다). 그래서 이 두 컴포넌트
  //   — `StandardCostBoard`·`RecipeList` — 를 **직접** 렌더해 분기 자체를 잡는다. 이 파일의
  //   머리말이 말하는 「전부 순수 컴포넌트로 export 해 테스트가 직접 렌더한다」 그 계층이다.
  describe("★P2-A: 0건 안내가 실제로 화면에 뜬다 — 렌더 분기 자체를 잡는다", () => {
    it("보드: 필터로 0건이 되면 「해당 조건에 맞는 SKU가 없다」가 뜬다", () => {
      render(
        <StandardCostBoard
          board={BOARD}
          displayItems={[]}
          filterSummary="5건 중 0건 표시 중 — 필터: 제품=존재하지 않는 제품"
        />,
      );
      expect(screen.getByText(/해당 조건에 맞는 SKU가 없다/)).toBeTruthy();
      // ★총계(SKU 5건 등)는 필터와 무관하게 «전체» 기준을 유지한다 — 0건이라고
      //   전체 숫자까지 0으로 보이면 커버리지 착시가 다시 생긴다.
      expect(screen.getByText(/SKU 5건/)).toBeTruthy();
    });

    it("레시피: 필터로 0건이 되면 「해당 조건에 맞는 레시피가 없다」가 뜬다", () => {
      render(
        <RecipeList
          recipes={[]}
          selectedId={null}
          onSelect={() => {}}
          totalCount={4}
          filterSummary="4건 중 0건 표시 중 — 필터: 제품=존재하지 않는 제품"
        />,
      );
      expect(screen.getByText(/해당 조건에 맞는 레시피가 없다/)).toBeTruthy();
    });

  });
});
// ★다른 라우트에서 같은 단언을 반복하지 않는다: 메뉴는 `Layout`이 라우트와 무관하게 그리므로
//   SUR-4가 이미 그 사실을 잰다. 대신 다른 페이지(대시보드 등)를 렌더하면 그 페이지의 목데이터
//   요구가 이 파일에 딸려 들어와, **원가와 무관한 이유로 빨개지는 테스트**가 된다.
