// @vitest-environment jsdom
//
// naverAdBepBasisSurface.test.tsx — 물류비·판매가의 **출처**가 사람 눈에 닿는가
// (「배송비 자(尺) 정합 목표」 계약 §4 합격기준 ⑤ · D-NAO-283).
//
// ## 왜 이 파일이 있어야 하나
// 이 계약의 «표면»은 백엔드가 basis를 계산하는 것이 **아니라** Jino가 BEP 표에서
// 「이 숫자가 실측인가 형제에서 빌린 값인가 그냥 가정인가」를 읽는 것이다.
//
// 백엔드 테스트는 `row.logistics_basis == "sibling"`을 지킨다. 그러나 그것은
// **「값이 만들어지나」**를 물을 뿐 **「사람이 그걸 보나」**를 못 묻는다 — 이 저장소가
// 반복해 밟은 병이고(전역 §4 「표면 절단 변이」), 직전 계약에서도 표면 절단 변이 6종이
// 프론트 1,196건을 전건 통과했다. 그래서 여기서 지키는 것은 로직이 아니라
// **DOM에 그 문자열이 있는가**다.
//
// 잡는 변이(전부 백엔드 테스트가 못 잡는 것):
//   M1 BepRowView에서 logisticsBasisNote 렌더 줄을 지운다
//   M2 priceBasisNote 렌더 줄을 지운다
//   M3 「배송 마진」 표시를 지운다 (ⓐ의 결과가 화면에서 사라진다)
//   M4 note 함수가 "sibling"/"default"를 구분하지 않고 같은 문자열을 낸다
//   M5 basis를 API 타입에서 빼 fixture가 컴파일은 되는데 값이 안 흐른다
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

type BepRow = import("../lib/api").NaverPerformanceBepRow;
type Breakdown = import("../lib/api").NaverPerformanceBepBreakdown;

const h = vi.hoisted(() => ({ bep: null as unknown }));

vi.mock("../lib/api", () => ({
  fetchNaverPerformanceBepBreakdown: () => Promise.resolve(h.bep),
  fetchNaverPerformanceDay: () => Promise.resolve(DAY),
  fetchNaverPerformanceCampaignOptions: () => Promise.resolve({ campaigns: [] }),
  fetchNaverPerformanceCampaign: () => Promise.resolve(null),
  fetchNaverPerformanceCompare: () => Promise.resolve(null),
  fetchNaverPerformanceBudget: () => Promise.resolve(null),
  fetchNaverPerformanceTimeline: () => Promise.resolve(null),
  fetchNaverOwnershipBands: () => Promise.resolve(null),
  fetchNaverOwnershipCampaigns: () => Promise.resolve(null),
}));

import NaverAdPerformance from "./NaverAdPerformance";

const DAY = {
  as_of: "2026-09-01T13:00:00",
  date: "2026-09-01",
  is_today: true,
  source: "confirmed",
  source_label: "네이버 확정 전환매출",
  data_note: "확정 적재 기준입니다.",
  data_gap_note: null,
  campaign_filter: null,
  campaigns: [],
  totals: { spend_today: 0, campaigns_active_today: 0, campaigns_total: 0 },
  today_actions: {
    executed_count: 0, blocked_count: 0, unknown_count: 0, items: [],
    quiet_reason: "실제로 반영된 변경이 없습니다.",
  },
};

/** prod 실측 모양(2026-09-01 시뮬레이션) — 픽스처가 prod와 같아야 결함을 잡는다. */
function row(over: Partial<BepRow>): BepRow {
  return {
    product_name: "상품",
    campaign_ids: ["cmp-1"],
    ad_count: 1,
    selling_price: 19900,
    commission_rate: 0.0425,
    commission_won: 846,
    cost_price: 6406,
    logistics_cost: -209,
    nbaesong_share: 0.2,
    nbaesong_sample: 10,
    logistics_basis: "orders",
    price_basis: "orders",
    pre_vat_margin: 12857,
    contribution_margin: 11688,
    bep_roas: 1.7026,
    target_roas: 1.958,
    ceiling_bid: null,
    ceiling_is_borrowed: false,
    ceiling_basis: "",
    market_bid: null,
    market_bid_device: null,
    market_bid_observed_on: null,
    market_bid_position: null,
    blocked_reason: "",
    sentence: "",
    ...over,
  };
}

function payload(rows: BepRow[]): Breakdown {
  return {
    rows,
    missing_cost_count: 0,
    vat_divisor: 1.1,
    data_note: "저장된 스냅샷 기준입니다.",
    campaign_id: null,
    as_of: "2026-09-01T13:00:00",
  };
}

async function draw(rows: BepRow[]) {
  h.bep = payload(rows);
  render(<MemoryRouter><NaverAdPerformance /></MemoryRouter>);
  // 표가 실제로 그려질 때까지 기다린다(로딩 카드에서 단언하면 아무것도 안 지킨다).
  await screen.findByText("손익분기");
}

afterEach(cleanup);

describe("BEP 표 — 이 숫자가 어디서 왔나 (D-NAO-283 합격기준 ⑤)", () => {
  it("형제 실측으로 채운 물류비는 «형제 상품 실측»이라고 말한다", async () => {
    await draw([row({ product_name: "4매입 폴드", logistics_basis: "sibling" })]);
    expect(screen.getByText("형제 상품 실측")).toBeTruthy();
  });

  it("자기도 형제도 주문이 없으면 «실측 없음(가정)»이라고 말한다 — 빈칸으로 두지 않는다", async () => {
    await draw([row({ logistics_basis: "default", logistics_cost: 1900 })]);
    expect(screen.getByText("실측 없음(가정)")).toBeTruthy();
  });

  it("실측이면 아무 말도 하지 않는다 — 모든 칸에 라벨을 달면 아무도 안 읽는다", async () => {
    await draw([row({ logistics_basis: "orders", price_basis: "orders" })]);
    expect(screen.queryByText("형제 상품 실측")).toBeNull();
    expect(screen.queryByText("실측 없음(가정)")).toBeNull();
    expect(screen.queryByText("스토어 할인가")).toBeNull();
    expect(screen.queryByText("손으로 넣은 값")).toBeNull();
  });

  it("세 물류비 출처가 **서로 다른** 문자열로 갈린다 (M4 — 같은 말을 하면 구분이 아니다)", async () => {
    await draw([
      row({ product_name: "A", logistics_basis: "orders" }),
      row({ product_name: "B", logistics_basis: "sibling" }),
      row({ product_name: "C", logistics_basis: "default", logistics_cost: 1900 }),
    ]);
    expect(screen.getAllByText("형제 상품 실측").length).toBe(1);
    expect(screen.getAllByText("실측 없음(가정)").length).toBe(1);
  });

  it("판매가 폴백 출처가 뜬다 — 스토어 할인가 / 손으로 넣은 값", async () => {
    await draw([
      row({ product_name: "A", price_basis: "meta" }),
      row({ product_name: "B", price_basis: "mapping" }),
    ]);
    expect(screen.getByText("스토어 할인가")).toBeTruthy();
    expect(screen.getByText("손으로 넣은 값")).toBeTruthy();
  });

  it("★ⓐ의 결과 — 음수 물류비는 «배송 마진»으로 화면에 뜬다", async () => {
    await draw([row({ logistics_cost: -1100 })]);
    expect(screen.getByText("배송 마진")).toBeTruthy();
    expect(screen.getByText("-1,100원")).toBeTruthy();   // 음수가 렌더된다
  });

  it("물류비가 양수면 «배송 마진»이라 하지 않는다", async () => {
    await draw([row({ logistics_cost: 1900 })]);
    expect(screen.queryByText("배송 마진")).toBeNull();
  });

  it("★발단 상품 — 판매가가 없던 4매입에 손익분기가 실제로 찍힌다", async () => {
    // prod 시뮬레이션 실측(2026-09-01): sp 0→19,900(meta) · 물류비 1,900→−209(sibling)
    //                                  · BEP None→1.7026
    await draw([row({
      product_name: "오하이Z플립폴드 외부 사생활보호 내부 지문방지액정보호필름",
      price_basis: "meta", logistics_basis: "sibling",
      selling_price: 19900, logistics_cost: -209, bep_roas: 1.7026,
    })]);
    expect(screen.getByText("스토어 할인가")).toBeTruthy();
    expect(screen.getByText("형제 상품 실측")).toBeTruthy();
    expect(screen.getByText("1.70배")).toBeTruthy();   // NO_DATA(—)가 아니라 숫자가 뜬다
  });
});
