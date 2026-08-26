// @vitest-environment jsdom
//
// otaoSettlementReachesTheUser.test.tsx — 「정산 창 (OTAO 지급)」이 **사람에게 닿는가** (계약 §4 S2)
//
// ## 왜 이 파일이 따로 있나
//
// n=6 적대 리뷰 P1-3이 남긴 것이 이 파일의 존재 이유다: 두 테스트 파일의 사각지대 **교집합** 탓에
// **prod 상태를 렌더하는 테스트가 저장소에 0건**이라, 판매 섹션을 통째로 지워도 18/18이 초록이었다.
// 그래서 여기 payload는 **prod 실측 7개 창을 그대로** 담고(2026-08-26 23:2x KST), `App`을
// `/otao-po`에서 통째로 렌더한다. 라우팅·페이지·패널·직렬화가 한 줄로 이어져야만 통과한다.
//
// 죽여야 할 표면 절단 변이:
//   SUR-S1  정산 섹션 자체를 페이지가 안 그림          → S2가 화면에서 사라진다
//   SUR-S2  창을 «하나만» 그림                          → 「1개 창 이상 대조」를 고를 수가 없다
//   SUR-S3  창 «기간»을 안 그림                         → 20/19일 경계가 안 보여 대조가 불가능하다
//   SUR-S4  부자재를 상품 칸에 **합쳐** 그림            → S1 픽업 누계와 왜 다른지 설명 불가
//   SUR-S5  `reconciled: null`을 「일치」/「불일치」로 그림 → 화면이 **없는 사실**을 말한다
//   SUR-S6  draft·창 경계 자백을 안 그림                → 대조 불일치의 첫 후보가 사라진다
//   SUR-S7  발주 원장이 비면 정산 섹션까지 지움         → 「픽업도 없다」로 읽힌다(원천이 다르다)
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import type { OtaoRoster, OtaoSettlement, OtaoSettlementWindow } from "../lib/api";

function w(
  key: string,
  start: string,
  end: string,
  shipments: number,
  pq: number,
  pa: number,
  mq: number,
  ma: number,
  extra: Partial<OtaoSettlementWindow> = {},
): OtaoSettlementWindow {
  return {
    key,
    start,
    end,
    shipments,
    lines: shipments * 10,
    product_quantity: pq,
    product_amount_cny: pa,
    material_quantity: mq,
    material_amount_cny: ma,
    other_quantity: 0,
    other_amount_cny: 0,
    total_amount_cny: pa + ma,
    shipment_ids: [],
    draft_shipment_ids: [],
    boundary_shipment_ids: [],
    payment_actual_cny: null,
    difference_cny: null,
    reconciled: null,
    ...extra,
  };
}

// ★prod 실측 7개 창 (2026-08-26). 합계 310,742 CNY · 선적 12 · draft 1 · 창 경계 3.
const SETTLEMENT: OtaoSettlement = {
  ledger_empty: false,
  ledger_start: "2026-01-27",
  ledger_end: "2026-08-18",
  currency: "CNY",
  windows: [
    w("2026-02", "2026-01-20", "2026-02-19", 2, 7060, 91057, 6500, 5200, {
      draft_shipment_ids: [9],
    }),
    w("2026-03", "2026-02-20", "2026-03-19", 1, 850, 10720, 7000, 5600, {
      boundary_shipment_ids: [11],
    }),
    w("2026-04", "2026-03-20", "2026-04-19", 1, 1150, 17180, 0, 0),
    w("2026-05", "2026-04-20", "2026-05-19", 3, 4500, 58405, 3000, 2400, {
      boundary_shipment_ids: [6],
    }),
    w("2026-06", "2026-05-20", "2026-06-19", 1, 2100, 26670, 3000, 2400),
    w("2026-07", "2026-06-20", "2026-07-19", 2, 4350, 56230, 1200, 960),
    w("2026-08", "2026-07-20", "2026-08-19", 2, 1750, 22400, 14400, 11520, {
      boundary_shipment_ids: [1],
    }),
  ],
  unassigned: { lines: 0, quantity: 0, amount_cny: 0 },
  totals: {
    windows: 7,
    shipments: 12,
    lines: 158,
    product_quantity: 21760,
    product_amount_cny: 282662,
    material_quantity: 35100,
    material_amount_cny: 28080,
    other_quantity: 0,
    other_amount_cny: 0,
    total_amount_cny: 310742,
    draft_shipments: 1,
    boundary_shipments: 3,
  },
  reconciliation: {
    payments_supplied: 0,
    windows_compared: 0,
    windows_matched: 0,
    matched_keys: [],
    mismatched: [],
    source: "none",
  },
  notes: [
    "창은 통관 원장이 덮는 구간(2026-01-27 ~ 2026-08-18)에서만 만든다. 그 이전 창은 픽업이 0이었던 것이 아니라 원장이 모르는 것이다.",
    "부자재(cleaning kits 등) 35,100개 28,080 CNY는 지급액에 들어가지만 S1의 «픽업 누계» 칸에는 안 들어간다(그 칸은 판매 SKU만 센다) — 두 숫자가 다른 이유다.",
    "실제 OTAO 지급액 원장이 이 저장소에 없어 «대조 불가»다(불일치가 아니다).",
  ],
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

let settlement: OtaoSettlement = SETTLEMENT;

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    // ★발주 원장을 «빈» 상태로 둔다 — 원천이 다르므로 그래도 정산 섹션은 살아 있어야 한다(SUR-S7).
    fetchOtaoRoster: vi.fn(async () => EMPTY_ROSTER),
    fetchOtaoSettlement: vi.fn(async () => settlement),
    fetchOtaoSales: vi.fn(async () => { throw new Error("이 파일은 정산 축만 잰다"); }),
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  settlement = SETTLEMENT;
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

/** 창 키가 든 `<tr>`을 찾는다 — 다른 행 숫자에 속지 않기 위해 항상 행 안에서 본다. */
async function windowRow(key: string): Promise<HTMLElement> {
  const cell = await screen.findByText(key);
  return cell.closest("tr") as HTMLElement;
}

describe("★S2 정산 창이 사람에게 닿는 경로", () => {
  it("SUR-S1: 「정산 창 (OTAO 지급)」 섹션이 화면에 뜬다", async () => {
    await renderApp();
    expect(
      await screen.findByRole("heading", { name: /정산 창 \(OTAO 지급\)/ }),
    ).toBeTruthy();
  });

  it("SUR-S2: prod 실측 **7개 창이 전부** 뜬다 — 하나만 그리면 대조할 창을 못 고른다", async () => {
    await renderApp();
    for (const key of ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]) {
      expect(await screen.findByText(key)).toBeTruthy();
    }
  });

  it("SUR-S3: 창 **기간**(전월 20 ~ 당월 19)이 화면에 적힌다 — 경계가 안 보이면 대조가 불가능하다", async () => {
    await renderApp();
    const row = await windowRow("2026-08");
    expect(row.textContent).toContain("2026-07-20 ~ 2026-08-19");
  });

  it("SUR-S4: ★상품과 부자재가 **다른 칸**으로 뜬다 — 합치면 S1 픽업 누계와의 차이를 설명 못 한다", async () => {
    await renderApp();
    await screen.findByText("2026-08");

    expect(screen.getByRole("columnheader", { name: "상품 CNY" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "부자재 CNY" })).toBeTruthy();

    const cells = Array.from((await windowRow("2026-08")).querySelectorAll("td")).map(
      (td) => td.textContent?.trim(),
    );
    expect(cells[3]).toBe("1,750"); // 상품 수량
    expect(cells[4]).toBe("22,400"); // 상품 CNY
    expect(cells[5]).toBe("14,400"); // 부자재 수량
    expect(cells[6]).toBe("11,520"); // 부자재 CNY
    expect(cells[7]).toBe("33,920"); // 픽업 합계
  });

  it("합계 줄이 prod 총액(310,742 CNY)을 그대로 그린다", async () => {
    await renderApp();
    const total = (await screen.findByText("합계")).closest("tr")!;
    expect(total.textContent).toContain("310,742");
    expect(total.textContent).toContain("282,662");
    expect(total.textContent).toContain("28,080");
  });

  it("SUR-S5: ★`reconciled: null`이 「대조 불가」로 그려진다 — 「일치」로도 「차액」으로도 그리지 않는다", async () => {
    await renderApp();
    const row = await windowRow("2026-08");
    expect(within(row).getByText("대조 불가")).toBeTruthy();
    expect(within(row).queryByText("일치")).toBeNull();
    expect(row.textContent).not.toContain("차액");
    // 실제 지급액 칸도 0이 아니라 «없음»이어야 한다.
    const cells = Array.from(row.querySelectorAll("td")).map((td) => td.textContent?.trim());
    expect(cells[8]).toBe("—");
  });

  it("SUR-S5b: 대조 불가라는 사실을 표 **앞에서** 먼저 말한다", async () => {
    await renderApp();
    expect(
      await screen.findByText(/지급액 대조 불가 — 실제 지급액 원장 없음/),
    ).toBeTruthy();
  });

  it("SUR-S6: draft·창 경계 선적을 자백한다 — 대조가 어긋났을 때 첫 번째 후보다", async () => {
    await renderApp();
    expect((await windowRow("2026-02")).textContent).toContain("미확정 1건");
    expect((await windowRow("2026-03")).textContent).toContain("창 경계 1건");
  });

  it("자백: 백엔드가 준 문장(부자재가 S1 픽업 누계에 없다)이 화면에 실린다", async () => {
    await renderApp();
    expect(
      await screen.findByText(/S1의 «픽업 누계» 칸에는 안 들어간다/),
    ).toBeTruthy();
  });

  it("SUR-S7: ★발주 원장이 비어 있어도 정산 섹션은 살아 있다 — 원천이 다르다", async () => {
    await renderApp();
    // 발주 쪽은 「비어 있다」를 말하고 있는데
    expect(await screen.findByText("발주 원장이 비어 있습니다")).toBeTruthy();
    // 정산 창은 그대로 보인다 — 묶으면 「픽업도 없다」로 거짓말한다
    expect(await screen.findByText("2026-08")).toBeTruthy();
    expect(screen.getByText(/지급액 대조 불가/)).toBeTruthy();
  });

  it("지급액을 받으면 「일치」로 바뀌고 대조 배지가 그것을 센다 (계약의 «1개 창 이상»)", async () => {
    settlement = {
      ...SETTLEMENT,
      windows: SETTLEMENT.windows.map((x) =>
        x.key === "2026-08"
          ? { ...x, payment_actual_cny: 33920, difference_cny: 0, reconciled: true }
          : x,
      ),
      reconciliation: {
        payments_supplied: 1,
        windows_compared: 1,
        windows_matched: 1,
        matched_keys: ["2026-08"],
        mismatched: [],
        source: "supplied",
      },
    };
    await renderApp();
    const row = await windowRow("2026-08");
    expect(within(row).getByText("일치")).toBeTruthy();
    expect(screen.getByText(/대조 1\/1 창 일치/)).toBeTruthy();
    // 값을 안 준 창은 여전히 「대조 불가」다 — 하나 맞았다고 나머지가 맞은 게 아니다.
    expect(within(await windowRow("2026-07")).getByText("대조 불가")).toBeTruthy();
  });

  it("불일치는 차액을 **숨기지 않고** 그린다", async () => {
    settlement = {
      ...SETTLEMENT,
      windows: SETTLEMENT.windows.map((x) =>
        x.key === "2026-08"
          ? { ...x, payment_actual_cny: 30000, difference_cny: 3920, reconciled: false }
          : x,
      ),
      reconciliation: { ...SETTLEMENT.reconciliation, payments_supplied: 1, windows_compared: 1, source: "supplied" },
    };
    await renderApp();
    expect((await windowRow("2026-08")).textContent).toContain("차액 3,920");
  });

  it("원장이 비면 「픽업 0」이 아니라 「데이터 없음」이라고 말한다", async () => {
    settlement = {
      ...SETTLEMENT,
      ledger_empty: true,
      ledger_start: null,
      ledger_end: null,
      windows: [],
    };
    await renderApp();
    expect(await screen.findByText(/「픽업 0」이 아니라 「데이터 없음」/)).toBeTruthy();
  });

  it("정산을 못 불러오면 화면이 **말한다** — 조용히 빈 자리가 되지 않는다", async () => {
    const api = await import("../lib/api");
    vi.mocked(api.fetchOtaoSettlement).mockRejectedValueOnce(new Error("boom"));
    await renderApp();
    expect(await screen.findByText(/정산 창을 불러오지 못했습니다/)).toBeTruthy();
  });
});
