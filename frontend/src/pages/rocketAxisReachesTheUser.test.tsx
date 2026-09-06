// @vitest-environment jsdom
//
// rocketAxisReachesTheUser.test.tsx — 종합조망 🚀 로켓배송(1P) 축이 **사람에게 닿는지**.
//
// ★★왜 이 파일이 생겼나 (2026-09-06 적대 리뷰, 트랙 S6):
//   이 화면은 2026-06-20 `bbff6f6c`로 prod에 올라간 뒤 **적대 리뷰도 전용 테스트도 0건**이었다.
//   도입 커밋이 스스로 *"이미 prod에 배포된 상태라 본 커밋은 배포된 현실을 기록한다"*고 적었다.
//   그 리뷰가 표면 절단 변이 3종을 넣었는데 **전부 SURVIVED** — 커버리지 배지를 지워도,
//   🚀 화면을 아예 안 그려도, 순이익 값을 비워도 **1,489개 테스트가 전건 초록**이었다.
//   🚀 탭을 «누르는» 테스트가 하나도 없었기 때문이다.
//
//   그래서 이 파일의 단언은 전부 **화면 텍스트**다. 값이 만들어지는지가 아니라
//   «사람이 그 값을 보는지»를 잰다. 호출 횟수·내부 상태는 세지 않는다.
//
// 이 파일이 지키는 것:
//   ① 커버리지 99.82%가 「100% 완전」으로 둔갑하지 않는다        (P1-1)
//   ② 「원가 매핑 관리」를 눌러도 페이지가 백지가 되지 않는다     (P1-2)
//   ③ 매핑을 바꾸면 위쪽 돈 숫자를 **다시 읽는다**               (P1-3)
//   ④ 커버리지를 «모를» 때 침묵하지 않는다                       (P2-3·P2-4)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    overviewCalls: 0,
    // ★기본 축(회계)이 먼저 렌더되므로 «최소한 그게 안 죽을 만큼»은 줘야 🚀 탭을 누를 수 있다.
    //   모양은 `rgSettlementAxisSurface.test.tsx`의 픽스처를 축약해 그대로 따른다.
    commandCenter: {
      period: { from: "2026-09-01", to: "2026-09-06" },
      account: {
        summary: {
          revenue: "5000000", return_deduction: "0", service_fee: "0", service_fee_vat: "0",
          total_fee: "100000", ad_spend: "50000", cost: "2000000", net_profit: "1500000",
          cost_covered_options: 10, option_count: 10,
          fee_rate_known_options: 0, fee_rate_default_options: 0,
          revenue_3p: "3000000", revenue_rg: "2000000", revenue_rg_basis: "console_net",
          rg_option_axis_days: "6/6", rg_option_axis_complete: true, rg_open_days: 0,
          rg_settlement_total: "0", rg_settlement_deducted: "0", rg_non_ad_deducted: "0",
          rg_flip_status: "applied_ex_ad", ad_nonpa_deducted: "0",
          seller_shipping_3p: "0", shipping_income_3p: "0", payable_vat: "0",
          rg_settlement_axis: "sales_date", rg_fee_basis: "settled_rate",
          rg_fee_rate: 0.105, rg_fee_coverage: 1, rg_fee_unmapped_revenue: 0,
        },
        by_option: [],
      },
      ad: { summary: { ad_spend: "50000", impressions: 0, clicks: 0, conv_revenue: "0", roas: null, ad_confirmed_applies: true }, by_option: [] },
      product: { summary: {}, by_option: [] },
    } as unknown,
    overview: null as unknown,
    // ★라이브 응답 모양 그대로 — 서버는 **봉투**를 준다(배열이 아니다).
    unmappedEnvelope: null as unknown,
    mappingEnvelope: null as unknown,
    upsertCalls: [] as unknown[],
  };
});

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  syncRealtime: () => Promise.resolve({}),
  // ★resolve해야 한다 — 🚀 축 렌더가 `{data && !loading && …}`로 **무관한 조회에 매여 있다**
  //   (적대 리뷰 P2-9: 3P/RG용 fetchCommandCenter가 실패·지연이면 로켓 화면이 안 뜬다).
  //   여기서는 그 구조를 그대로 두고 테스트가 통과하도록 최소 객체를 준다 — 그 결합 자체는
  //   이번 스코프 밖이라 P2로 이월한다.
  fetchCommandCenter: () => Promise.resolve(h.commandCenter),
  fetchRevenueReconcile: () => h.pending(),
  fetchCoupangAdReport: () => h.pending(),
  fetchRocketPromoPnl: () => h.pending(),
  patchPromotionManual: () => h.pending(),
  fetchRocketOverview: () => { h.overviewCalls += 1; return Promise.resolve(h.overview); },
  fetchRocketCostMapUnmapped: () => Promise.resolve(h.unmappedEnvelope),
  fetchRocketCostMap: () => Promise.resolve(h.mappingEnvelope),
  upsertRocketCostMap: (b: unknown) => { h.upsertCalls.push(b); return Promise.resolve({}); },
  excludeRocketCostMap: () => Promise.resolve({}),
  deleteRocketCostMap: () => Promise.resolve({ deleted: 1 }),
}));

import CommandCenter from "./CommandCenter";

/** prod 실측(2026-09-06 18:16 KST, `localhost:8001/api/overview/rocket-overview`).
 *  ★숫자는 라이브 값을 그대로 쓴다 — `coverage_pct`가 **문자열** `"0.9982"`인 것까지. */
function overview(over: Record<string, unknown> = {}) {
  return {
    revenue: "12345678", ad_spend: "58964", cost: "3930478.86",
    net_profit: "4356235.14", has_cost: true,
    po_count: 100, order_qty: 1000,
    cost_coverage: {
      coverage_pct: "0.9982",
      detail_order_amount: "11131685", unmapped_order_amount: "20160",
      pos_without_detail_count: 0, confirmed_sku_count: 219,
      excluded_sku_count: 22, unmapped_sku_count: 2,
    },
    drift: { settled_amount: "0", drift_amount: "0", drift_pct: null },
    ...over,
  };
}

const UNMAPPED_ENVELOPE = {
  vendor_id: null, total_unmapped: 105, returned: 1,
  items: [{
    product_number: "P-TEST-001", product_name: "오하이 강화유리 테스트",
    barcode: null, total_order_qty: 10, po_count: 2,
    suggestions: [{
      internal_sku: "OHI-TGLASS-IP17PRO", score: 0.71,
      product_name: "강화유리 아이폰17프로", cost_price: 1691, already_mapped_count: 12,
    }],
  }],
};
const MAPPING_ENVELOPE = { count: 1, mappings: [{
  product_number: "P-DONE-001", internal_sku: "OHI-X", status: "confirmed",
  match_method: "suggested", product_name: "확정된 상품", barcode: null,
  note: "auto score=0.70", cost_price: 1000,
}] };

async function openRocketAxis() {
  render(<CommandCenter />);
  fireEvent.click(await screen.findByText(/🚀 로켓배송 1P/));
  await screen.findByText(/로켓배송\(1P\) — 오하이테크 발주 돈 축/);
}

beforeEach(() => {
  h.overviewCalls = 0;
  h.overview = overview();
  h.unmappedEnvelope = UNMAPPED_ENVELOPE;
  h.mappingEnvelope = MAPPING_ENVELOPE;
  h.upsertCalls = [];
});
afterEach(cleanup);

describe("🚀 로켓배송(1P) 축 — 사람에게 닿는가", () => {
  it("화면 자체가 그려진다", async () => {
    // ★표면 절단 변이 M2(<RocketView/>를 안 그림)를 잡는 자리다.
    await openRocketAxis();
    expect(await screen.findByText(/원가 커버리지/)).toBeTruthy();
  });

  it("순이익 값이 화면에 뜬다", async () => {
    // ★표면 절단 변이 M3(순이익 value를 비움)를 잡는 자리다.
    await openRocketAxis();
    expect(await screen.findByText("4,356,235원")).toBeTruthy();
  });

  // ══════════════════════════════════════════════════════════════
  // P1-1 — 99.82%가 「100% 완전」으로 둔갑하면 안 된다
  // ══════════════════════════════════════════════════════════════
  it("커버리지 99.82%를 「100%」로 올려 말하지 않는다", async () => {
    await openRocketAxis();
    // ★`Math.round(0.9982*100)`은 100이다. 그 한 줄이 이 화면의 자백 장치를 꺼 뒀었다.
    expect(await screen.findByText(/원가 커버리지 99\.8%/)).toBeTruthy();
    expect(screen.queryByText(/원가 커버리지 100%/)).toBeNull();
  });

  it("100% 미만이면 «과대 가능» 경고가 뜬다", async () => {
    await openRocketAxis();
    expect(await screen.findByText(/net_profit 원가 과소반영/)).toBeTruthy();
    // 순이익 카드도 「완전한 값」이라 단정하지 않는다
    expect(screen.queryByText("매출−광고−원가")).toBeNull();
  });

  it("커버리지가 진짜 100%면 경고를 안 붙인다 — 늑대소년이 되지 않는다", async () => {
    h.overview = overview({
      cost_coverage: { ...(overview().cost_coverage as object), coverage_pct: "1.0",
        unmapped_order_amount: "0", unmapped_sku_count: 0 },
    });
    await openRocketAxis();
    expect(await screen.findByText(/원가 커버리지 100%/)).toBeTruthy();
    expect(screen.queryByText(/net_profit 원가 과소반영/)).toBeNull();
  });

  // ══════════════════════════════════════════════════════════════
  // P2-3·P2-4 — «모를 때»가 «완전할 때»보다 조용하면 안 된다
  // ══════════════════════════════════════════════════════════════
  it("원가가 하나도 안 붙었으면(has_cost=false) 그 사실을 화면이 말한다", async () => {
    h.overview = overview({ has_cost: false, cost: null, cost_coverage: null });
    await openRocketAxis();
    // ★종전엔 배지가 `has_cost && cov`로 게이팅돼 **가장 나쁜 경우에 통째로 사라졌다**.
    expect(await screen.findByText(/원가가 하나도 반영되지 않았습니다/)).toBeTruthy();
  });

  it("커버리지를 모르면 「모름」이라 쓴다 — 0%라는 없는 정밀도를 만들지 않는다", async () => {
    h.overview = overview({
      cost_coverage: { ...(overview().cost_coverage as object), coverage_pct: null },
    });
    await openRocketAxis();
    expect(await screen.findByText(/원가 커버리지 모름/)).toBeTruthy();
    expect(screen.queryByText(/원가 커버리지 0%/)).toBeNull();
    // ★★「모름」을 «완전»으로 취급하면 안 된다 — 자백 배지가 초록으로 바뀌고 고치는 법이
    //   사라진다. 변이 M7(`covComplete = covRatio == null || …`)이 여기서 죽는다.
    expect(await screen.findByText(/원가 매핑 관리'에서 미매핑 상품번호를 확정하면/)).toBeTruthy();
    // 색은 배지 «바깥» div가 쥔다(제목 div가 아니라 그 부모).
    const badge = screen.getByText(/원가 커버리지 모름/).closest("div")?.parentElement;
    expect(badge?.className ?? "").toContain("amber");
    expect(badge?.className ?? "").not.toContain("emerald");
  });

  // ══════════════════════════════════════════════════════════════
  // P1-2 — 「원가 매핑 관리」가 페이지를 죽이면 안 된다
  // ══════════════════════════════════════════════════════════════
  describe("원가 매핑 관리", () => {
    it("열어도 페이지가 백지가 되지 않고 미매핑 목록이 뜬다", async () => {
      // ★도입 이래 2개월 반 동안 여기서 `unmapped.map is not a function`이 던져
      //   document.body가 통째로 비었다. 서버는 배열이 아니라 봉투를 준다.
      await openRocketAxis();
      fireEvent.click(await screen.findByText(/🔗 원가 매핑 관리/));
      expect(await screen.findByText("P-TEST-001")).toBeTruthy();
      expect(await screen.findByText(/오하이 강화유리 테스트/)).toBeTruthy();
    });

    it("미매핑 개수는 절단된 목록 길이가 아니라 «총수»를 쓴다", async () => {
      // 서버는 total_unmapped=105를 주는데 items는 1건뿐이다(limit 절단).
      await openRocketAxis();
      fireEvent.click(await screen.findByText(/🔗 원가 매핑 관리/));
      await screen.findByText("P-TEST-001");
      const body = document.body.textContent ?? "";
      expect(body).toMatch(/105/);
    });

    it("확정하면 위쪽 돈 숫자를 다시 읽는다", async () => {
      // ★P1-3: 종전엔 목록만 다시 읽어서 커버리지·순이익이 그대로였고,
      //   사람은 「✅ 확정」을 보고도 위가 안 변하니 같은 걸 또 눌렀다.
      await openRocketAxis();
      fireEvent.click(await screen.findByText(/🔗 원가 매핑 관리/));
      await screen.findByText("P-TEST-001");
      const before = h.overviewCalls;
      fireEvent.click(screen.getByText(/OHI-TGLASS-IP17PRO/));
      await waitFor(() => expect(h.overviewCalls).toBeGreaterThan(before));
      expect(h.upsertCalls.length).toBe(1);
    });
  });
});
