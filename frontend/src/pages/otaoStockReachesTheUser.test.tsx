// @vitest-environment jsdom
//
// otaoStockReachesTheUser.test.tsx — 「자사 현재고 (파생)」이 **사람에게 닿는가** (계약 §4 S4)
//
// ## 왜 이 파일이 따로 있나
//
// 이 트랙은 같은 병을 **세 번** 밟았다: n=6 P1-3(prod 상태를 렌더하는 테스트 0건) → n=7 P1-2
// (그 병을 적어 둔 파일 안에서 기본 목을 «빈 원장»으로 둬 같은 구멍을 한 칸 옆에 팜). 둘 다
// 「값은 맞는데 사람에게 안 닿는다」였고, 둘 다 단위 테스트는 초록이었다.
//
// ⇒ **기본 목은 «prod가 지금 있을 모양»이다** — 발주 원장 있음 + 재고 스냅샷 있음. 빈 분기는
//   SUR-T6·T7이 따로 덮는다. 그리고 `App`을 `/otao-po`에서 통째로 렌더해 라우팅·페이지·패널·
//   직렬화가 한 줄로 이어져야만 통과하게 한다.
//
// 죽여야 할 표면 절단 변이:
//   SUR-T1  재고 섹션 자체를 페이지가 안 그림              → S4가 화면에서 사라진다
//   SUR-T2  ★「− 판매」를 **0으로** 그림                    → 재고가 부풀고 「발주 마라」로 읽힌다
//   SUR-T3  파생값 자리에 «상한»을 그림                    → 상한을 현재고로 오독한다
//   SUR-T4  창고를 **합쳐서** 그림                          → 본사와 쿠팡 제트가 한 숫자가 된다
//   SUR-T5  실사 대조 오차 칸을 안 그림                     → 계약이 지목한 그 숫자가 사라진다
//   SUR-T6  발주 원장이 비면 재고 섹션까지 지움             → 「재고도 없다」로 읽힌다(원천이 다르다)
//   SUR-T7  스냅샷 0개를 «재고 0»으로 그림                  → 안 찍은 것과 없는 것이 같아진다
//   SUR-T8  역할 미상 창고 자백을 안 그림                   → 모르는 재고가 조용히 사라진다
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { OtaoRoster, OtaoStock, OtaoStockRow } from "../lib/api";

function row(
  code: string,
  baselineOwn: number | null,
  inbound: number,
  extra: Partial<OtaoStockRow> = {},
): OtaoStockRow {
  return {
    product_code: code,
    baseline_quantity: baselineOwn,
    baseline_by_role: baselineOwn === null ? {} : { own: baselineOwn },
    inbound_quantity: inbound,
    // ★판매는 언제나 null이다 — 다리가 없다. 이 파일이 지키는 것의 절반이 이 한 줄이다.
    sold_quantity: null,
    derived_quantity: null,
    derived_blocked_by: baselineOwn === null ? "baseline" : "sold",
    upper_bound_if_no_sales: baselineOwn === null ? null : baselineOwn + inbound,
    counted_quantity: null,
    counted_at: null,
    counted_warehouse: null,
    counted_warehouse_role: null,
    counted_axis_mismatch: false,
    latest_snapshot_quantity: baselineOwn,
    variance_vs_snapshot: null,
    variance_pct: null,
    variance_vs_derived: null,
    ...extra,
  };
}

// ★prod 실측 창고 구성(2026-08-25 16:42:46 · ref 98 §8): 본사·본사-포장·반품창고·
//   쿠팡 제트배송·아마존 5개. 「본사에 있는 것」과 「제트에 나가 있는 것」은 정반대 의미다.
const STOCK: OtaoStock = {
  snapshot_empty: false,
  snapshot_count: 2,
  baseline_at: "2026-08-27T10:00:00",
  latest_at: "2026-09-03T10:00:00",
  counted_at: "2026-09-03T18:00:00",
  counted_from: "2026-09-03T18:00:00",
  counted_axis_mismatches: [],
  inbound_window_start: "2026-08-27",
  sold_unavailable_reason:
    "판매를 이 축에 못 붙인다 — 발주·픽업은 OTAO 품목코드(GAPIP…) 축이고 판매는 우리 SKU(internal_sku) 축인데 두 집합의 교집합이 0이고 이어 주는 표가 prod에 없다.",
  rows: [
    {
      ...row("GAPIP16PR", 340, 1000),
      baseline_by_role: { own: 340, material: 900, channel: 120 },
      latest_snapshot_quantity: 340,
      counted_quantity: 320,
      counted_at: "2026-09-03T18:00:00",
      counted_warehouse: "본사",
      counted_warehouse_role: "own",
      variance_vs_snapshot: 20,
      variance_pct: 6.25,
    },
    { ...row("GAPIP15", 11, 0), baseline_by_role: { own: 11, excluded: 8 } },
    row("GAPIP17PR", null, 500),
  ],
  unknown_warehouses: [{ warehouse: "제3창고", quantity: 77 }],
  totals: {
    sku_count: 3,
    baseline_own: 351,
    latest_own: 351,
    inbound: 1500,
    sold: null,
    derived: null,
    counted_sku_count: 1,
    variance_sku_count: 1,
    variance_abs_sum: 20,
    counted_without_snapshot: [],
  },
  notes: [
    "기준 2026-08-27 10:00 → 최신 2026-09-03 10:00 KST, 서로 다른 스냅샷 2개.",
    "판매를 이 축에 못 붙인다 — 다리 구축은 이 계약의 「안 함」이다.",
    "역할을 모르는 창고가 있다(제3창고) — 본사 재고에 합치지 않았다.",
  ],
};

const EMPTY_STOCK: OtaoStock = {
  snapshot_empty: true,
  snapshot_count: 0,
  baseline_at: null,
  latest_at: null,
  counted_at: null,
  counted_from: null,
  counted_axis_mismatches: [],
  inbound_window_start: null,
  sold_unavailable_reason: STOCK.sold_unavailable_reason,
  rows: [],
  unknown_warehouses: [],
  totals: {},
  notes: [
    "ECOUNT 재고 스냅샷이 아직 하나도 없다 — 「재고 0」이 아니라 «찍은 적 없음»이다.",
  ],
};

// ★prod가 실제로 있는 상태 — 이게 Jino가 보는 분기다(정본 발주서 66건).
const FULL_ROSTER: OtaoRoster = {
  ledger_empty: false,
  window_start: "2026-01-27",
  rows: [
    {
      product_code: "GAPIP15PR",
      ordered: 500,
      picked: 300,
      reserved: 200,
      out_of_window_ordered: 4000,
      last_order_date: "2026-06-01",
      order_count: 2,
    },
  ],
  totals: {
    ordered: 500,
    picked: 300,
    reserved: 200,
    out_of_window_ordered: 4000,
    unmapped_qty: 0,
    sku_count: 1,
    unmapped_name_count: 0,
  },
  unmapped: [],
  notes: [],
  source: {
    orders_total: 95,
    orders_authoritative: 66,
    orders_superseded: 29,
    last_order_date: "2026-06-01",
    name_map_total: 65,
    name_map_resolved: 43,
  },
};

const EMPTY_ROSTER: OtaoRoster = {
  ledger_empty: true,
  window_start: null,
  rows: [],
  totals: {},
  unmapped: [],
  notes: [],
  source: {
    orders_total: 0,
    orders_authoritative: 0,
    orders_superseded: 0,
    last_order_date: null,
    name_map_total: 0,
    name_map_resolved: 0,
  },
};

let stock: OtaoStock = STOCK;
let roster: OtaoRoster = FULL_ROSTER;

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    // ★기본은 «원장 있음 + 스냅샷 있음»이다 — Jino가 보는 분기라야 표면 절단이 잡힌다.
    fetchOtaoRoster: vi.fn(async () => roster),
    fetchOtaoStock: vi.fn(async () => stock),
    fetchOtaoSettlement: vi.fn(async () => {
      throw new Error("이 파일은 재고 축만 잰다");
    }),
    fetchOtaoSales: vi.fn(async () => {
      throw new Error("이 파일은 재고 축만 잰다");
    }),
    fetchHealth: vi.fn(async () => {
      throw new Error("not needed");
    }),
    fetchSchedulerStatus: vi.fn(async () => {
      throw new Error("not needed");
    }),
  };
});

beforeEach(() => {
  stock = STOCK;
  roster = FULL_ROSTER;
  window.history.pushState({}, "", "/otao-po");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderApp() {
  const { default: App } = await import("../App");
  return render(<App />);
}

/** 상품코드가 든 `<tr>`을 «파생 현재고 표에서» 찾는다 — 역할 표에도 같은 코드가 있다. */
async function stockRow(code: string): Promise<HTMLElement> {
  const cells = await screen.findAllByText(code);
  const tr = cells
    .map((c) => c.closest("tr") as HTMLElement | null)
    .find((t) => t?.querySelectorAll("td").length === 9);
  if (!tr) throw new Error(`파생 현재고 표에서 ${code} 행을 못 찾았다`);
  return tr;
}

describe("★S4 자사 현재고가 사람에게 닿는 경로", () => {
  it("SUR-T1: 「자사 현재고 (파생)」 섹션이 화면에 뜬다", async () => {
    await renderApp();
    expect(
      await screen.findByRole("heading", { name: /자사 현재고 \(파생\)/ }),
    ).toBeTruthy();
  });

  it("SUR-T2: ★「− 판매」 칸이 **0이 아니라 「근거 없음」**으로 뜬다", async () => {
    await renderApp();
    const tr = await stockRow("GAPIP16PR");
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim());
    // 칸 자리: 코드 / 기준 / 입고 / 판매 / 파생 / 상한 / 최신 / 실사 / 오차
    expect(cells[3]).toBe("근거 없음");
    expect(cells[3]).not.toBe("0");
    expect(screen.getByRole("columnheader", { name: "− 판매" })).toBeTruthy();
  });

  it("SUR-T3: 파생 현재고가 **「산출 불가」**로 뜬다 — 상한 숫자로 대체되지 않는다", async () => {
    await renderApp();
    const tr = await stockRow("GAPIP16PR");
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[4]).toBe("산출 불가");
    // 상한은 **다른 칸**에 있고 「≤」가 붙어 현재고가 아님을 말한다.
    expect(cells[5]).toBe("≤ 1,340");
    expect(cells[4]).not.toContain("1,340");
  });

  it("SUR-T3b: 기준 재고가 없는 코드는 「기준 없음」이지 「재고 0」이 아니다", async () => {
    await renderApp();
    const tr = await stockRow("GAPIP17PR");
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[1]).toBe("—"); // 기준 재고 — 0이 아니다
    expect(cells[4]).toBe("기준 없음");
  });

  it("SUR-T4: ★창고가 **역할별로 갈라져** 뜬다 — 본사와 쿠팡 제트가 한 숫자가 되면 안 된다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /창고 역할별 기준 재고/ });
    expect(screen.getByRole("columnheader", { name: "본사" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "쿠팡 제트배송" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "본사-포장(부자재)" })).toBeTruthy();

    const roleRow = (await screen.findAllByText("GAPIP16PR"))
      .map((c) => c.closest("tr") as HTMLElement | null)
      .find((t) => t?.querySelectorAll("td").length === 6);
    expect(roleRow).toBeTruthy();
    const cells = Array.from(roleRow!.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[1]).toBe("340"); // own
    expect(cells[2]).toBe("900"); // material
    expect(cells[3]).toBe("120"); // channel
    // 합계(1,360)가 어디에도 «한 숫자로» 서지 않는다
    expect(cells.join("|")).not.toContain("1,360");
  });

  it("SUR-T5: ★실사 대조 오차가 화면에 뜬다 — 계약이 지목한 그 숫자다", async () => {
    await renderApp();
    const tr = await stockRow("GAPIP16PR");
    expect(screen.getByRole("columnheader", { name: "대조 오차" })).toBeTruthy();
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[7]).toContain("320"); // 사람이 센 값 (+ 어느 창고인지 — SUR-T11)
    expect(cells[8]).toContain("+20"); // ECOUNT 340 − 실사 320
    expect(cells[8]).toContain("6.3%");
  });

  it("SUR-T5b: 실사 안 한 코드의 오차 칸은 «미실시»지 0이 아니다", async () => {
    await renderApp();
    const tr = await stockRow("GAPIP15");
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[8]).toBe("—");
    expect(cells[8]).not.toBe("0");
  });

  it("SUR-T6: ★발주 원장이 비어도 재고 섹션은 살아 있다 — 원천이 다르다", async () => {
    roster = EMPTY_ROSTER;
    await renderApp();
    expect(await screen.findByText(/발주 원장이 비어 있습니다/)).toBeTruthy();
    expect(
      await screen.findByRole("heading", { name: /자사 현재고 \(파생\)/ }),
    ).toBeTruthy();
    // 파생 표와 역할 표 둘 다에 같은 코드가 있다 — «둘 다» 있어야 정상이다.
    expect((await screen.findAllByText("GAPIP16PR")).length).toBeGreaterThan(0);
  });

  it("SUR-T7: ★스냅샷 0개를 «재고 0»이 아니라 «찍은 적 없음»으로 말한다", async () => {
    stock = EMPTY_STOCK;
    await renderApp();
    expect(await screen.findByText(/자사 재고 스냅샷이 아직 없습니다/)).toBeTruthy();
    // EmptyState 사유 + notes 둘 다에 나온다 — 「재고 0」으로 읽히지 않게 두 곳에서 말한다.
    expect((await screen.findAllByText(/찍은 적 없음/)).length).toBeGreaterThan(0);
    // 빈 표를 그려서 「전부 0」으로 읽히게 하지 않는다
    expect(screen.queryByRole("columnheader", { name: "= 파생 현재고" })).toBeNull();
  });

  it("SUR-T8: 역할 미상 창고를 자백한다 — 모르는 재고가 조용히 사라지지 않는다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /창고 역할별 기준 재고/ });
    expect(await screen.findByText(/역할 미상 1곳/)).toBeTruthy();
    expect(screen.getByText(/제3창고\(77\)/)).toBeTruthy();
  });

  it("SUR-T9: 판매를 못 붙이는 «이유»가 화면에 글자로 있다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /자사 현재고 \(파생\)/ });
    const hits = screen.getAllByText(/교집합이 0/);
    expect(hits.length).toBeGreaterThan(0);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 적대 리뷰 1R 상환 — 살아남은 표면 변이를 닫는다
// ══════════════════════════════════════════════════════════════════════════

describe("★1R 생존 변이 상환", () => {
  it("SUR-T10: notes 배너가 화면에 뜬다 — 초판은 배너를 통째로 지워도 안 잡혔다", async () => {
    await renderApp();
    await screen.findByRole("heading", { name: /자사 현재고 \(파생\)/ });
    // t0(기준 시각)가 사람에게 닿는 **유일한 표면**이 이 배너다.
    expect(await screen.findByText(/기준 2026-08-27 10:00 → 최신/)).toBeTruthy();
  });

  it("SUR-T11: ★실사 «어느 창고를» 셌는지가 칸에 뜬다 — 없으면 옆 칸 오차가 무엇 대비인지 모른다", async () => {
    await renderApp();
    const tr = await stockRow("GAPIP16PR");
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[7]).toContain("320");
    expect(cells[7]).toContain("본사");
  });

  it("SUR-T12: ★기준 창고가 아닌 곳을 센 행은 «오차»가 아니라 「축 다름」으로 뜬다", async () => {
    stock = {
      ...STOCK,
      counted_axis_mismatches: ["GAPIP16PR"],
      rows: STOCK.rows.map((r) =>
        r.product_code === "GAPIP16PR"
          ? {
              ...r,
              counted_quantity: 900,
              counted_warehouse: "본사-포장",
              counted_warehouse_role: "material",
              counted_axis_mismatch: true,
              variance_vs_snapshot: -560,
              variance_pct: -62.2,
            }
          : r,
      ),
    };
    await renderApp();
    const tr = await stockRow("GAPIP16PR");
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim());
    // 숫자를 「오차」라 부르지 않는다 — 그건 서로 다른 창고를 뺀 값이다.
    expect(cells[8]).toContain("축 다름");
    expect(cells[8]).not.toContain("-560");
  });
});
