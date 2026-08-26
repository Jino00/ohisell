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
    quantity_ambiguous: 7,
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
  dates: ["2026-08-24", "2026-08-25", "2026-08-26"],
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
      quantity_ambiguous: 0,
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
      quantity_ambiguous: 0,
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
      series: [40, 0, 100],
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
    // ★S2 정산 축도 같은 화면에 산다. 여기서 일부러 «실패»시키는 이유는 판매 축과 같다 —
    //   한쪽이 죽어도 이 파일이 재는 축은 그대로 보여야 한다. 그 격리를 mock이 매번 검사한다.
    fetchOtaoSettlement: vi.fn(async () => { throw new Error("이 파일은 판매 축만 잰다"); }),
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

// ─────────────────────────────────────────────────────────────────────────────
// ★적대 리뷰 P1-3 — **prod가 타는 경로**를 보는 테스트가 없었다.
//
// 기제는 두 파일의 사각지대 «교집합»이었다: 이 파일은 로스터를 비우고(`ledger_empty: true`),
// `otaoPoReachesTheUser.test.tsx`는 `fetchOtaoSales`를 일부러 throw시킨다. 그래서
// **「원장이 비어 있지 않다 + 판매 fetch가 정상」** — 즉 prod의 실제 상태 — 를 렌더하는
// 테스트가 저장소에 한 건도 없었고, 그 상태에서 판매 섹션을 통째로 지워도 18/18이 초록이었다.
//
// 아래 describe가 그 교집합을 메운다. prod 실측(발주서 95건 적재됨)과 같은 모양이다.
// ─────────────────────────────────────────────────────────────────────────────

const LOADED_ROSTER: OtaoRoster = {
  ledger_empty: false,
  window_start: "2026-01-27",
  rows: [
    {
      product_code: "GAPIP15PR",
      ordered: 3900,
      picked: 2100,
      reserved: 1800,
      out_of_window_ordered: 61400,
      last_order_date: "2026-08-12",
      order_count: 37,
    },
  ],
  totals: {
    ordered: 30090,
    picked: 18970,
    reserved: 11120,
    out_of_window_ordered: 310927,
    unmapped_qty: 2790,
    sku_count: 75,
    unmapped_name_count: 22,
  },
  unmapped: [{ item_name: "For iPhone 15 Pro", quantity: 400 }],
  notes: ["예약 잔량은 통관 원장이 덮는 창(2026-01-27 이후) 안의 발주분만 센다."],
  source: {
    orders_total: 95,
    orders_authoritative: 66,
    orders_superseded: 29,
    last_order_date: "2026-08-19",
    name_map_total: 65,
    name_map_resolved: 43,
  },
};

describe("prod가 타는 경로 — 원장이 차 있고 판매도 정상일 때", () => {
  beforeEach(async () => {
    const api = await import("../lib/api");
    vi.mocked(api.fetchOtaoRoster).mockResolvedValue(LOADED_ROSTER);
  });

  it("★발주 3칸과 판매 섹션이 **둘 다** 뜬다", async () => {
    await renderApp();
    // 발주 축 (S1)
    expect(await screen.findByText("GAPIP15PR")).toBeTruthy();
    expect(await screen.findByText(/합계 — 발주 30,090 · 픽업 18,970 · 잔량 11,120/)).toBeTruthy();
    // 판매 축 (S3) — 이게 없으면 이 커밋이 아무것도 안 한 것과 같다
    expect(await screen.findByText("판매 (채널 통합)")).toBeTruthy();
    expect(await screen.findByText("OHI-0001")).toBeTruthy();
    expect(await screen.findByText("채널별 판매 — 매핑률과 결손일")).toBeTruthy();
    expect(await screen.findByText("SKU별 채널 통합 판매수량")).toBeTruthy();
  });

  it("두 축이 **다른 라벨 공간**임을 화면이 말한다", async () => {
    await renderApp();
    // 발주 축 라벨(GAPIP…)과 판매 축 라벨(OHI-…)이 같은 화면에 있되 다른 표에 있다
    expect(await screen.findByText("GAPIP15PR")).toBeTruthy();
    expect(await screen.findByText("OHI-0001")).toBeTruthy();
    expect(await screen.findByText(/발주 축 ↔ 판매 축 다리: 겹치는 값 0개/)).toBeTruthy();
    expect(
      await screen.findByText(/위 발주 표의 상품코드\(GAPIP…\)와 \*\*다른 축\*\*/),
    ).toBeTruthy();
  });

  it("SUR-S7 — SKU별 **일별 시계열**이 화면에 그려진다", async () => {
    const { container } = await renderApp();
    await screen.findByText("OHI-0001");
    // 창 합계만 그리면 「언제 팔렸나」가 사라진다 — 그건 S3 원문의 첫 요구를 안 한 것이다
    const spark = container.querySelector('svg[role="img"] polyline');
    expect(spark).toBeTruthy();
    expect(spark?.getAttribute("points")?.split(" ").length).toBe(3);
    expect(await screen.findByText(/일별 추이 \(2026-08-24 ~ 2026-08-26\)/)).toBeTruthy();
  });

  it("SUR-S8 — 「매핑 모호」 수량이 화면에 뜬다", async () => {
    await renderApp();
    // 다수결로 고르지 않고 남긴 몫. 안 그리면 P1-1 수정이 사용자에게 안 닿는다.
    expect(await screen.findByText(/^7 ⚠$/)).toBeTruthy();
    expect(await screen.findByText("매핑 모호")).toBeTruthy();
  });
});
