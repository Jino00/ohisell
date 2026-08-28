// @vitest-environment jsdom
//
// costHomeSurface.test.tsx — 「💰 원가」 **홈 탭**(D-CPP-62 S2)이 사람에게 실제로 닿는가.
//
// `costHome.test.ts`가 규칙(함수)을 재고, `costHome.tsx`의 순수 컴포넌트가 이미 있다고 해서
// 그것들이 **실제로 CostPage.tsx에서 불리는지**는 별개의 질문이다 — 이 저장소가 반복 밟은
// 「함수는 값을 만드는데 사람은 못 본다」(호출부 삭제·렌더 삭제가 단위 테스트를 다 통과하고
// 산다). 그래서 여기는 `App`을 `/cost`에서 통째로 렌더한다 — **아무 탭도 안 누른 채** 홈의
// 내용이 실제 DOM 픽셀이 되는지를 잰다.
//
// 재는 것 다섯:
//  H1 홈이 기본 탭 — 아무것도 안 눌러도 보드·인박스·왕복 표가 뜬다
//  H2 보드 스트립 숫자가 `/api/cost/board` payload **그대로**다 — 화면이 따로 계산 안 한다
//  H3 인박스 — 0건 묶음은 「— 없음」이고 이동 버튼은 비활성이다 (계약 §3 「없음」≠「0」)
//  H4 인박스 항목 클릭 → 부자재 드릴다운(기존 패널)으로 실제로 간다
//  H5 왕복 표 — 단가 없는 행은 빈 칸(「—」)이지 「0원」이 아니고, [다운로드]는 S3까지 비활성
//
// ★픽스처는 prod 실측(2026-08-28) 비율을 흉내낸다 — 단가원천은 manual이 절대다수이고
//   ledger는 소수다(이 저장소가 이미 한 번 밟은 「픽스처가 전부 ledger」 오염을 반복하지 않는다).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import type {
  CostBoard,
  CostMaterial,
  CostRecipe,
  CostSetting,
  CostTableCensus,
} from "../lib/api";
import { EMPTY_IS_NOT_ZERO } from "../lib/costHome";

// ══════════════════════════════════════════════════════════════════
// 픽스처
// ══════════════════════════════════════════════════════════════════

function baseMaterial(over: Partial<CostMaterial>): CostMaterial {
  return {
    id: 0,
    name: "?",
    unit: "ea",
    category: "부자재",
    status: "approved",
    excel_label: null,
    excel_ref_price: null,
    match_rule: null,
    form_factor: null,
    part: null,
    note: null,
    lot_count: 0,
    price_count: 0,
    stale_count: 0,
    latest_price_ex_vat: null,
    latest_price_inc_vat: null,
    latest_price_inc_derived: false,
    latest_price_source: null,
    latest_price_effective_date: null,
    price_rule: "latest",
    lot_price_min: null,
    lot_price_max: null,
    lot_price_has_span: false,
    price_conflict: false,
    price_conflict_price_id: null,
    prices: [],
    used_by: [],
    used_by_count: 0,
    ...over,
  };
}

// M1 — 단가 있음(등록가·manual). 「단가 있는 종」 분자에 들어간다.
const M1 = baseMaterial({
  id: 1,
  name: "지문방지필름 TPU 3매",
  form_factor: "fold",
  part: "필름",
  price_count: 1,
  lot_count: 0,
  latest_price_ex_vat: "600",
  latest_price_inc_vat: "660",
  latest_price_source: "manual",
  latest_price_effective_date: "2026-08-18",
});

// M2 — 단가 없음, 엑셀 참고값은 있다 → 인박스 note가 그 값을 말한다.
const M2 = baseMaterial({
  id: 2,
  name: "비닐(16*23+4)",
  form_factor: "flip",
  part: "필름",
  excel_ref_price: "168",
});

// M3 — 단가 있음(원장·ledger) + 모순(price_conflict) → 왕복 표에 배지 둘이 함께 선다.
const M3 = baseMaterial({
  id: 3,
  name: "부착 안내문",
  form_factor: "fold",
  part: "필름",
  price_count: 2,
  lot_count: 2,
  latest_price_ex_vat: "55.0",
  latest_price_inc_vat: "60.5",
  latest_price_source: "ledger",
  price_conflict: true,
  price_conflict_price_id: 991,
});

// M4 — 단가 없음, 엑셀 참고값도 없음, 폼팩터도 없음(null) → 인박스 note는 null, 표는 「—」.
const M4 = baseMaterial({
  id: 4,
  name: "지그 부속",
});

const MATERIALS = [M1, M2, M3, M4];

function baseRecipe(over: Partial<CostRecipe>): CostRecipe {
  return {
    id: 0,
    product_name: "?",
    form_factor: null,
    status: "draft",
    source: "excel",
    recipe_kind: "assembly",
    form_source: "rule",
    anomaly_flag: null,
    approved_at: null,
    match: null,
    line_count: 0,
    link_count: 0,
    standard: {
      computable: false,
      std_cost_ex_vat: null,
      std_cost_inc_vat: null,
      reason: null,
      unresolved: [],
      partial_ex_vat: null,
      partial_inc_vat: null,
      line_count: 0,
      lines: [],
    },
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
    ...over,
  };
}

// R1 — price_conflict. 원가표 항목 T1과 **같은 사건**(레시피 45·97 실측 모양의 재현)이다.
const R1 = baseRecipe({
  id: 45,
  product_name: "지문방지필름 TPU 3매",
  form_factor: "fold",
  anomaly_flag: "price_conflict:부착 안내문:55.0≠30.0",
});

// R2 — anomaly 없는 깨끗한 레시피. 어느 인박스 묶음에도 안 잡힌다.
const R2 = baseRecipe({ id: 60, product_name: "다른 제품", form_factor: "flip" });

const RECIPES = [R1, R2];
// ★일부러 no_recipe_match·needs_manual_lines 레시피를 하나도 안 둔다 — 「구성 없음」
//   묶음이 0건인 채로 실제 화면에 「— 없음」이 뜨는지를 이 파일이 재려는 것이다.

// T1 — R1과 같은 사건(price_conflict, 픽 완료) → 넷째 묶음에서 **접혀야** 한다.
// T2 — 픽 안 됨, 이상 없음 → 넷째 묶음에 남는다.
const TABLE_CENSUS: CostTableCensus = {
  items: [
    {
      id: 45,
      section: "부자재",
      item_name: "지문방지필름 TPU 3매",
      form_factor: "fold",
      recipe_kind: "assembly",
      total_inc_vat: "2350.7",
      row_number: 12,
      anomalies: "price_conflict:부착 안내문:55.0≠30.0",
      line_count: 3,
      picked: true,
      picked_by_recipe_id: 45,
    },
    {
      id: 70,
      section: "부자재",
      item_name: "미확정 항목",
      form_factor: null,
      recipe_kind: "assembly",
      total_inc_vat: null,
      row_number: 40,
      anomalies: null,
      line_count: 1,
      picked: false,
      picked_by_recipe_id: null,
    },
  ],
  total: 2,
  picked_count: 1,
  last_uploaded_at: "2026-08-20T01:23:45",
};

// ★분모가 재료 배열과 안 겹치는 값이어야 「화면이 board를 그대로 읽는가」를 잴 수 있다
//   (materials.length=4인데 board 숫자가 우연히 4나 배수로 겹치면 재계산과 구별이 안 된다).
const BOARD: CostBoard = {
  items: [],
  sku_count: 13,
  computed_count: 7,
  uncomputed_count: 6,
  recipe_count: 20,
  approved_recipe_count: 8,
};

const SETTINGS: CostSetting[] = [
  { key: "valuation_method", value: "fifo", confirmed: false, note: null, updated_at: null },
  { key: "standard_price_rule", value: "latest", confirmed: true, note: null, updated_at: null },
];

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchCostMaterials: vi.fn(async () => ({ items: MATERIALS })),
    fetchCostLedgerMaterialLines: vi.fn(async () => ({ items: [] })),
    fetchCostSettings: vi.fn(async () => ({ items: SETTINGS })),
    fetchCostRecipes: vi.fn(async () => ({ items: RECIPES })),
    fetchCostBoard: vi.fn(async () => BOARD),
    fetchCostTableCensus: vi.fn(async () => TABLE_CENSUS),
    fetchCostSettingHistory: vi.fn(async () => ({ items: [] })),
    fetchCostAutoRefreshRuns: vi.fn(async () => ({ items: [] })),
    fetchCostAutoRefreshQueue: vi.fn(async () => ({ items: [] })),
    getSchedulerHealth: vi.fn(async () => ({ healthy: true })),
    getAdCostCookieStatus: vi.fn(async () => ({})),
    getCollectionStatus: vi.fn(async () => ({ streams: [] })),
    fetchApi: vi.fn(async () => ({ jobs: [], items: [] })),
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

async function renderApp() {
  const { default: App } = await import("../App");
  const result = render(<App />);
  await screen.findByRole("heading", { name: /원가/ });
  // ★홈이 기본 탭이므로 여기서는 «아무 탭도 누르지 않는다» — 이 파일의 요점이 정확히
  //   그 상태에서 뜨는 내용이다. 데이터가 실제로 들어찬 뒤에 이후 단언이 안전해지도록
  //   보드 스트립의 한 타일만 기다린다.
  await screen.findByTestId("cost-home-board-strip");
  return result;
}

describe("H1·H2: 홈이 기본 탭이고, 보드 숫자는 board payload를 그대로 쓴다", () => {
  it("아무 탭도 안 눌러도 보드·인박스·왕복 표 셋이 다 뜬다", async () => {
    await renderApp();
    expect(screen.getByTestId("cost-home-board-strip")).toBeTruthy();
    expect(screen.getByTestId("cost-home-inbox")).toBeTruthy();
    expect(screen.getByTestId("cost-home-roundtrip")).toBeTruthy();
  });

  it("보드 타일 넷의 숫자가 board payload·부자재 배열 그대로다 — 화면이 재계산하지 않는다", async () => {
    await renderApp();
    // ★분자(단가 있는 종=2: M1·M3)/분모(전체=4)는 materials 배열에서 오고,
    //   나머지 셋은 BOARD 픽스처의 값 그대로다. 어느 하나라도 다른 숫자면 재계산이거나
    //   호출부가 다른 값을 보고 있다는 뜻이다.
    expect(within(screen.getByTestId("board-tile-materials")).getByText("2/4")).toBeTruthy();
    expect(within(screen.getByTestId("board-tile-sku")).getByText("7/13")).toBeTruthy();
    expect(within(screen.getByTestId("board-tile-recipes")).getByText("8/20")).toBeTruthy();
    expect(within(screen.getByTestId("board-tile-uncomputed")).getByText("6건")).toBeTruthy();
  });

  it("마지막 업로드 시각이 뜬다 — naive UTC를 KST로 보여준다(한 번도 안 올린 것과 다른 사실)", async () => {
    await renderApp();
    const tile = screen.getByTestId("board-tile-upload");
    expect(within(tile).queryByText("한 번도 안 올렸다")).toBeNull();
    // 정확한 서식은 `formatKstDateTime` 자체 테스트의 몫이다 — 여기서는 «호출부가
    // last_uploaded_at을 실제로 그린다»만 잰다.
    expect(tile.textContent).not.toBe("");
  });

  it("분모의 뜻을 화면이 자백한다 — 「원가 있는 SKU」가 product_master 전체가 아니다", async () => {
    await renderApp();
    expect(screen.getByTestId("board-strip-denominator-note")).toBeTruthy();
  });
});

describe("H3: 할 일 인박스 — 0건과 «없음»이 같은 화면이 되지 않는다", () => {
  it("구성 없음 묶음은 0건이다 — 「— 없음」이라고 말하고 이동 버튼은 비활성이다", async () => {
    await renderApp();
    const group = screen.getByTestId("inbox-group-no-recipe");
    expect(within(group).getByTestId("inbox-count-no-recipe").textContent).toBe("— 없음");
    expect(
      (within(group).getByTestId("inbox-goto-no-recipe") as HTMLButtonElement).disabled,
    ).toBe(true);
    // 0건이면 「펼쳐 보기」 자체가 없다 — 펼칠 것이 없다.
    expect(within(group).queryByText(/펼쳐 보기/)).toBeNull();
  });

  it("모순 묶음 — 레시피 45의 price_conflict가 1건으로 뜬다", async () => {
    await renderApp();
    const group = screen.getByTestId("inbox-group-conflict");
    expect(within(group).getByTestId("inbox-count-conflict").textContent).toBe("1건");
  });

  it("★★중복 접기가 화면에서도 지켜진다 — 넷째 묶음(원가표 항목)은 T2 하나뿐이다", async () => {
    // T1(id=45)은 R1과 같은 price_conflict 사건이라 이미 모순 묶음이 셌다 — 여기 또 서면
    // 같은 사건이 두 줄에 선 것이고, 그게 설계 Q1이 막으려는 결함이다.
    await renderApp();
    const group = screen.getByTestId("inbox-group-cost-table");
    expect(within(group).getByTestId("inbox-count-cost-table").textContent).toBe("1건");
    fireEvent.click(within(group).getByText(/펼쳐 보기/));
    expect(within(group).getByTestId("inbox-row-cost-table-70")).toBeTruthy();
    expect(within(group).queryByTestId("inbox-row-cost-table-45")).toBeNull();
  });

  it("단가 없음 묶음 — M2·M4 둘 다 뜨고, 엑셀 참고값이 있는 쪽만 그 사실을 말한다", async () => {
    await renderApp();
    const group = screen.getByTestId("inbox-group-no-price");
    expect(within(group).getByTestId("inbox-count-no-price").textContent).toBe("2건");
    fireEvent.click(within(group).getByText(/펼쳐 보기/));
    expect(within(group).getByText(/비닐\(16\*23\+4\)/)).toBeTruthy();
    expect(within(group).getByText(/엑셀 참고값 168/)).toBeTruthy();
    expect(within(group).getByText(/지그 부속/)).toBeTruthy();
  });
});

describe("H4: 인박스 항목을 누르면 실제로 부자재 드릴다운(기존 패널)으로 간다", () => {
  it("「단가 없음」 항목(M2)을 누르면 부자재 탭으로 이동하고 그 종의 상세가 뜬다", async () => {
    await renderApp();
    const group = screen.getByTestId("inbox-group-no-price");
    fireEvent.click(within(group).getByText(/펼쳐 보기/));
    fireEvent.click(screen.getByTestId("inbox-row-no-price-2"));
    // ★드릴다운 = 기존 부자재 탭의 상세 패널. 홈의 껍데기가 아니라 **그 화면**으로 갔다는
    //   증거는 이 헤더 문구다(`CostPage.tsx`: `「{selected.name}」 단가 이력`).
    expect(await screen.findByText(`「${M2.name}」 단가 이력`)).toBeTruthy();
    expect(screen.getByTestId("material-list-scroll")).toBeTruthy();
  });
});

describe("H5: 왕복 표 — 빈 칸은 0원이 아니고, [다운로드]는 S3까지 비활성이다", () => {
  it("단가 없는 행(M2)의 단가 칸은 「—」다 — 「0원」이 아니다", async () => {
    await renderApp();
    const row = screen.getByTestId(`roundtrip-row-${M2.id}`);
    const cells = within(row).getAllByRole("cell");
    // 열 순서: id·이름·폼팩터·부품·단위·단가(제외)·단가(포함)·...
    expect(cells[5].textContent).toBe("—");
    expect(cells[6].textContent).toBe("—");
    expect(within(row).queryByText(/0원/)).toBeNull();
    // ★「없음」과 「0」을 가르는 그 자백이 배지 툴팁에도 실제로 실린다.
    const badge = within(row).getByTestId(`roundtrip-badge-${M2.id}-no-price`);
    expect(badge.title).toContain(EMPTY_IS_NOT_ZERO);
  });

  it("폼팩터가 없는 종(M4)은 「—」로 뜬다 — 빈 칸을 조작 없이 두지 않는다", async () => {
    await renderApp();
    const row = screen.getByTestId(`roundtrip-row-${M4.id}`);
    const cells = within(row).getAllByRole("cell");
    expect(cells[2].textContent).toBe("—"); // 폼팩터
    expect(cells[3].textContent).toBe("—"); // 부품
  });

  it("모순·원장정본 배지가 «함께» 뜬다(M3) — 렌더가 하나만 살아남는 변이를 잡는다", async () => {
    await renderApp();
    const row = screen.getByTestId(`roundtrip-row-${M3.id}`);
    expect(within(row).getByTestId(`roundtrip-badge-${M3.id}-conflict`)).toBeTruthy();
    expect(within(row).getByTestId(`roundtrip-badge-${M3.id}-ledger`)).toBeTruthy();
  });

  it("[다운로드] 버튼은 비활성이고 「S3에서 만든다」가 보인다(계약 §1 — S3는 이번 범위 밖)", async () => {
    await renderApp();
    const btn = screen.getByTestId("roundtrip-download") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toContain("S3에서 만든다");
  });

  it("행을 누르면 부자재 드릴다운으로 간다 — 왕복 표도 인박스와 같은 길을 쓴다", async () => {
    await renderApp();
    fireEvent.click(screen.getByTestId(`roundtrip-row-${M1.id}`));
    expect(await screen.findByText(`「${M1.name}」 단가 이력`)).toBeTruthy();
  });
});

describe("H6: 기존 3탭이 여전히 있고, 홈에서 직행이 된다 (설계 Q2 — 익숙한 손이 안 깨진다)", () => {
  it("「부자재」를 누르면 부자재 탭으로 간다", async () => {
    await renderApp();
    fireEvent.click(screen.getByRole("button", { name: "부자재" }));
    expect(await screen.findByTestId("material-list-scroll")).toBeTruthy();
  });

  it("「레시피」를 누르면 레시피 탭으로 간다", async () => {
    await renderApp();
    fireEvent.click(screen.getByRole("button", { name: "레시피" }));
    expect(await screen.findByTestId("recipe-list-scroll")).toBeTruthy();
  });

  it("「표준원가 보드」를 누르면 보드 탭으로 간다", async () => {
    await renderApp();
    fireEvent.click(screen.getByRole("button", { name: "표준원가 보드" }));
    // ★이 픽스처의 board.items는 빈 배열이다 — 보드 탭 고유의 0건 안내를 재는 것으로
    //   충분하다(집계 자체는 `costPageReachesTheUser.test.tsx` SUR-7·SUR-8의 몫).
    expect(
      await screen.findByText(/보드에 실릴 SKU가 없다/),
    ).toBeTruthy();
  });

  it("홈으로 다시 돌아올 수 있다", async () => {
    await renderApp();
    fireEvent.click(screen.getByRole("button", { name: "부자재" }));
    await screen.findByTestId("material-list-scroll");
    fireEvent.click(screen.getByRole("button", { name: "홈" }));
    expect(await screen.findByTestId("cost-home-roundtrip")).toBeTruthy();
  });
});
