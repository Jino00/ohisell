// @vitest-environment jsdom
//
// otaoSalesReachesTheUser.test.tsx — S3 판매 축이 **사람에게 실제로 닿는가** (계약 §4 S3)
//
// ## 왜 이 파일이 따로 있나
//
// 서비스층 12건·HTTP 4건이 전부 초록이어도 화면이 그 값을 **안 그리면** 아무도 못 본다.
// n=4가 정확히 그 상태로 미달했고("코드는 있으나 아무도 못 본다"), n=6의 S3도 같은 함정의
// 표면이 넓다 — 자백 필드가 넷이라 하나만 안 그려도 화면이 조용히 거짓말한다.
//
// 죽여야 할 «최종 표면 절단» 변이 여섯:
//
//   SUR-S1  판매 섹션 자체를 화면이 안 그림             → S3이 통째로 없는 것과 같다
//   SUR-S2  **매핑률**을 안 그림                        → 얼마나 못 붙었는지 아무도 모른다
//   SUR-S3  `mapping_rate: null`을 **0%로 그림**        → 「잴 수 없음」이 「전부 실패」로 읽힌다
//   SUR-S4  **「구분 근거 없음」**을 안 그림(0일로 표기) → 없는 근거를 있는 척한다(계약 §2-8)
//   SUR-S5  **「데이터 없음 N일」**을 안 그림            → 결손이 「판매 0」으로 읽힌다
//   SUR-S6  **발주↔판매 다리 자백**을 안 그림           → 두 표가 같은 줄로 읽혀 거짓 대비가 된다
//
// **`App`을 통째로 `/otao-po`에서 렌더한다** — 라우팅·페이지·패널·직렬화가 한 줄로 이어져야만
// 통과한다. 그리고 **발주 로스터는 일부러 비워 둔다**: 판매 축이 발주와 «독립»으로 서야
// 한다는 것 자체가 요구사항이기 때문이다(원장이 비어도 판매는 보여야 한다).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { OtaoRoster, OtaoSales } from "../lib/api";

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

function channel(over: Partial<OtaoSales["channels"][number]>) {
  return {
    key: "naver",
    label: "네이버 스마트스토어",
    company: "주식회사 오하이",
    sell_type: "스마트스토어",
    source_table: "orders",
    bridge: "orders.product_id",
    rows: 10,
    quantity: 100,
    quantity_mapped: 90,
    quantity_excluded: 4,
    mapping_rate: 90,
    days_with_rows: 58,
    missing_day_evidence: true,
    days_collected_zero: ["2026-08-20"],
    days_no_data: ["2026-08-21", "2026-08-22"],
    ...over,
  } as OtaoSales["channels"][number];
}

const SALES: OtaoSales = {
  window_start: "2026-06-28",
  window_end: "2026-08-26",
  days: 60,
  channels: [
    channel({}),
    // ★근거가 «없는» 채널 — 결손을 가르면 안 되고 화면이 그렇게 말해야 한다
    channel({
      key: "wing3p_ofix",
      label: "쿠팡 Wing 3P — 오픽스",
      company: "개인회사 오픽스",
      sell_type: "3P",
      source_table: "coupang_vendor_item_sales_daily",
      bridge: "vendor_item_id → product_channel_mapping",
      quantity: 50,
      quantity_mapped: 50,
      quantity_excluded: 0,
      mapping_rate: 100,
      missing_day_evidence: false,
      days_collected_zero: [],
      days_no_data: [],
    }),
    // ★판매가 0이라 매핑률을 «잴 수 없는» 채널 — 0%와 다른 상태다
    channel({
      key: "rg2p_ohitech",
      label: "쿠팡 로켓그로스 2P — 오하이테크",
      company: "주식회사 오하이테크",
      sell_type: "2P",
      source_table: "coupang_rg_order_item",
      bridge: "vendor_item_id → product_channel_mapping",
      rows: 0,
      quantity: 0,
      quantity_mapped: 0,
      quantity_excluded: 0,
      mapping_rate: null,
      days_with_rows: 0,
      missing_day_evidence: false,
      days_collected_zero: [],
      days_no_data: [],
    }),
  ],
  rows: [
    {
      internal_sku: "OHI-0001",
      product_name: "지문방지 PET 필름 2매, 아이폰16",
      total: 140,
      by_channel: { naver: 90, wing3p_ofix: 50 },
    },
  ],
  daily: [{ date: "2026-08-26", total: 140, by_channel: { naver: 90, wing3p_ofix: 50 } }],
  unmapped: [{ channel: "naver", quantity: 10 }],
  order_axis: {
    order_axis_codes: 75,
    sales_axis_skus: 963,
    overlap: 0,
    order_codes_reached_by_name_map: 43,
    note: "발주 축 라벨과 판매 축 라벨은 겹치는 값이 0개다. 다리가 없으면 같은 줄에 놓을 수 없다.",
  },
  notes: [
    "결손일과 「판매 0」을 구분할 근거가 **없는** 채널: 쿠팡 Wing 3P — 오픽스.",
    "★발주 축(`product_code`)과 이 판매 축(`internal_sku`)을 잇는 다리가 아직 없다",
  ],
};

let sales: OtaoSales = SALES;

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchOtaoRoster: vi.fn(async () => EMPTY_ROSTER),
    fetchOtaoSales: vi.fn(async () => sales),
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  sales = SALES;
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

describe("S3 판매 축이 화면까지 닿는다", () => {
  it("SUR-S1 — 발주 원장이 비어 있어도 판매 섹션이 뜬다", async () => {
    await renderApp();
    expect(await screen.findByText("판매 (채널 통합)")).toBeTruthy();
    // 채널 라벨이 실제 픽셀이 되는 자리는 셋이고, 셋 다 이유가 있다:
    //   ①채널별 표의 행 ②SKU 표의 열 머리(없으면 이름 없는 숫자 열이 된다)
    //   ③「매핑 필요」 카드(그 채널에 못 붙은 판매가 있을 때만)
    // 네이버는 미매핑이 있어 셋 다, Wing 3P는 미매핑이 없어 둘이다.
    expect((await screen.findAllByText("네이버 스마트스토어")).length).toBe(3);
    expect((await screen.findAllByText("쿠팡 Wing 3P — 오픽스")).length).toBe(2);
    expect(await screen.findByText("OHI-0001")).toBeTruthy();
    expect(await screen.findByText("지문방지 PET 필름 2매, 아이폰16")).toBeTruthy();
  });

  it("SUR-S2 — 채널별 매핑률이 화면에 뜬다", async () => {
    await renderApp();
    expect(await screen.findByText("90%")).toBeTruthy();
    expect(await screen.findByText("100%")).toBeTruthy();
  });

  it("SUR-S3 — 잴 수 없는 매핑률을 «0%»로 그리지 않는다", async () => {
    await renderApp();
    // 「잴 수 없음」이라고 말해야 한다
    expect(await screen.findByText("잴 수 없음")).toBeTruthy();
    // 그리고 0%라고는 절대 안 쓴다 — 그건 「전부 실패」로 읽힌다
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("SUR-S4 — 결손 구분 근거가 없는 채널을 그렇게 표기한다", async () => {
    await renderApp();
    const cells = await screen.findAllByText("구분 근거 없음");
    // 근거 없는 채널이 둘(Wing 3P · RG 2P)이므로 둘 다 그려져야 한다
    expect(cells.length).toBe(2);
  });

  it("SUR-S5 — 「데이터 없음 N일」이 결손으로 뜬다(판매 0과 갈라서)", async () => {
    await renderApp();
    expect(await screen.findByText(/데이터 없음 2일/)).toBeTruthy();
    expect(await screen.findByText(/판매 0 1일/)).toBeTruthy();
  });

  it("SUR-S6 — 발주 축과의 다리가 없다는 자백이 화면에 뜬다", async () => {
    await renderApp();
    expect(await screen.findByText(/발주 축 ↔ 판매 축 다리: 겹치는 값 0개/)).toBeTruthy();
  });

  it("취소·반품으로 뺀 몫이 화면에 남는다", async () => {
    await renderApp();
    expect(await screen.findByText("−4")).toBeTruthy();
  });

  it("못 붙은 판매 수량이 「매핑 필요」로 뜬다", async () => {
    await renderApp();
    expect(
      await screen.findByText("매핑 필요 — SKU 시계열에서 빠져 있는 판매"),
    ).toBeTruthy();
  });

  it("판매 조회가 실패해도 발주 화면이 통째로 사라지지 않는다", async () => {
    const api = await import("../lib/api");
    vi.mocked(api.fetchOtaoSales).mockRejectedValueOnce(new Error("boom"));
    await renderApp();
    // 발주 쪽 자백은 그대로 뜬다
    expect(await screen.findByText("발주 원장이 비어 있습니다")).toBeTruthy();
    // 그리고 판매 실패를 «말한다» — 조용히 빈 화면이 되지 않는다
    expect(await screen.findByText(/판매 시계열을 불러오지 못했습니다/)).toBeTruthy();
  });
});
