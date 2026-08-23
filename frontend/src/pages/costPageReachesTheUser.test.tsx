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
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import type {
  CostBoard,
  CostLedgerMaterialLine,
  CostMaterial,
  CostRecipe,
  CostSetting,
} from "../lib/api";

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
  ],
  sku_count: 3,
  computed_count: 2,
  uncomputed_count: 1,
  recipe_count: 2,
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
    fetchCostRecipes: vi.fn(async () => ({ items: [RECIPE] })),
    fetchCostBoard: vi.fn(async () => BOARD),
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
    expect(screen.getByText(/레시피 미승인 — 계산 안 함/)).toBeTruthy();
    // 미계산 행의 표준원가 칸은 「—」다 — 0원으로 그리면 미입력이 확정값으로 둔갑한다.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
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
  });
});
// ★다른 라우트에서 같은 단언을 반복하지 않는다: 메뉴는 `Layout`이 라우트와 무관하게 그리므로
//   SUR-4가 이미 그 사실을 잰다. 대신 다른 페이지(대시보드 등)를 렌더하면 그 페이지의 목데이터
//   요구가 이 파일에 딸려 들어와, **원가와 무관한 이유로 빨개지는 테스트**가 된다.
