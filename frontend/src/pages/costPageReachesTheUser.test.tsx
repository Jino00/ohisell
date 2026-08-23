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
import { cleanup, render, screen, within } from "@testing-library/react";

import type {
  CostLedgerMaterialLine,
  CostMaterial,
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
});
// ★다른 라우트에서 같은 단언을 반복하지 않는다: 메뉴는 `Layout`이 라우트와 무관하게 그리므로
//   SUR-4가 이미 그 사실을 잰다. 대신 다른 페이지(대시보드 등)를 렌더하면 그 페이지의 목데이터
//   요구가 이 파일에 딸려 들어와, **원가와 무관한 이유로 빨개지는 테스트**가 된다.
