// @vitest-environment jsdom
//
// rocket1pRevenueAdLedger.test.tsx — 손익 사다리가 **광고 원장을 남김없이 보이는가**.
//
// 왜 생겼나(Jino 2026-08-09): *"광고비가 실제 광고비보다 적게 계산되고 있어."*
//   맞았다. 옵션 광고 원장의 네 통 중 **둘이 화면에 한 줄도 없었다**:
//     구멍1 판매O·손익밖(라이브 08-08 33,750원) · 구멍2 팔린 옵션의 판매 없는 날(7일 창 435,916원).
//   백엔드가 그 둘을 이름과 금액으로 내놓아도 **화면이 안 그리면 사용자에겐 그대로 없는 돈**이다.
//   백엔드 테스트가 값을 지키고, 이 파일이 «그 값이 눈에 닿는가»를 지킨다.
//
// 이 파일이 죽이는 변이(각 테스트에 Mn 표기):
//   M1 구멍1 줄 삭제 · M2 구멍2 줄 삭제 · M3 미포함인데 «−» 부호를 붙임(차감된 것처럼 보임)
//   M4 포함인데 부호 없음 · M5 각주가 통 하나를 빼먹음 · M6 「제외」를 결정으로 단정
//   M7 사유 미기록을 빈 칸으로 그림
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, within, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Rocket1PRevenue as R } from "../lib/api";

const h = vi.hoisted(() => ({ fetchRevenue: vi.fn() }));
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchRocket1PRevenue: h.fetchRevenue,
}));

import Rocket1PRevenue from "./Rocket1PRevenue";

/** 라이브 2026-08-08을 그대로 옮긴 값 — 네 통이 797,431원으로 딱 맞는다. */
const BASE: R = {
  period: { from: "2026-08-08", to: "2026-08-08", vendor_id: "A01029796" },
  totals: {
    qty: 210, consumer_revenue: "3817270", our_revenue: "2318513",
    settlement_revenue: "1686890", ad_spend: "797241.00",
    our_share: "0.6074", roas: "2.9082",
  },
  pnl: {
    basis: "costed_subset", qty: 200, revenue: "2198728", cost: "863821.70",
    promo_burden: "195000", ad_spend: "664449", vat: "43223.40",
    net_profit: "432233.90", profit_rate: "0.1966",
    ad_no_sales: "99232", ad_no_sales_days: "0", ad_uncosted: "33750",
    ad_unattributed: "132982", ad_no_sales_included: false,
    ad_account_total: "797241.00", ad_option_total: "797431",
    cost_coverage: "0.9483", revenue_priced: "2318513",
    promo_burden_known: true, blocked: null,
    uncosted: {
      skus: 1, qty: 1, our_revenue: "11940", our_revenue_partial: false,
      actionable_skus: 1, link_missing_skus: 1, cost_missing_skus: 0,
      excluded_skus: 0, loss_confirmed_skus: 0,
      new_skus: 0, new_our_revenue: "0", new_sku_window_days: 90,
      top: [{
        sku_id: "59459681", product_name: "Z폴드SE 필름 세트", qty: 1,
        our_revenue: "11940", consumer_revenue: "18300", loss_confirmed: false,
        reason: "no_link", is_new: false, first_sold_at: "2026-06-05",
        first_po_at: "2025-07-22", first_sold_at_bounded: false,
      }],
      excluded_top: [], excluded_our_revenue: "0",
    },
    note: "",
  },
  daily: [],
  coverage: {
    sales_data_covered: true, sales_data_from: "2026-06-01", sales_data_to: "2026-08-08",
    qty_axis: 210, qty_all: 210, qty_priced: 210, priced_pct: "1.0000",
    options_unpriced: 0, note: "",
  },
  freshness: {
    days_expected: 1, days_with_data: 1, days_no_data: 0,
    data_as_of: "2026-08-08", lag_days: 0, stale: false, note: "",
  },
  ad_reconciliation: {
    option_sum: "797431", account_total: "797241.00", diff: "190.00", basis: "",
  },
  option_count: 0, shown: 0, options: [], axes_note: "",
};

/** 깊은 구조 일부만 갈아끼운다 — 스프레드로 덮으면 uncosted가 통째로 사라진다. */
function withPnl(over: Partial<R["pnl"]>, uncostedOver: Partial<R["pnl"]["uncosted"]> = {}): R {
  return {
    ...BASE,
    pnl: { ...BASE.pnl, ...over, uncosted: { ...BASE.pnl.uncosted, ...uncostedOver } },
  };
}

function mount(data: R) {
  h.fetchRevenue.mockReset();
  h.fetchRevenue.mockResolvedValue(data);
  return render(<MemoryRouter><Rocket1PRevenue /></MemoryRouter>);
}

afterEach(() => { cleanup(); h.fetchRevenue.mockReset(); });

/** 조각을 담은 **잎 노드**를 찾는다.
 *
 *  ★조상까지 매칭되면 «여러 개 찾음»으로 죽는다 — 부모 <div>·<td>의 textContent도 조각을
 *    담기 때문이다. 자식 엘리먼트가 없는 노드로 좁혀야 «그 글자가 실제로 찍힌 자리»를 잡는다.
 */
function leafText(fragment: string) {
  return (_t: string, el: Element | null) =>
    !!el && el.children.length === 0 && (el.textContent ?? "").includes(fragment);
}

/** 광고 원장 각주 <p>. 조상 <div>가 아니라 그 문단 자체를 잡는다. */
async function footnote(): Promise<HTMLElement> {
  return screen.findByText(
    (_t, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes("옵션 광고 원장"));
}

/** 사다리 줄 하나를 라벨 조각으로 찾아 «라벨 + 금액»을 담은 행을 돌려준다. */
async function ladderLine(fragment: string): Promise<HTMLElement> {
  const label = await screen.findByText(leafText(fragment));
  const row = label.closest("div.flex");
  expect(row, `«${fragment}» 줄을 못 찾았다`).not.toBeNull();
  return row as HTMLElement;
}

describe("손익 사다리 — 광고 원장 네 통", () => {
  it("M1 원가 미상 옵션의 광고비가 **줄로** 보인다 (예전엔 어느 줄에도 없었다)", async () => {
    mount(BASE);
    const row = await ladderLine("원가 미상 옵션");
    expect(within(row).getByText("33,750원")).toBeTruthy();
  });

  it("M3 미포함인 통에는 «−» 부호를 붙이지 않는다 — 차감된 것처럼 보이면 안 된다", async () => {
    mount(BASE);
    // 원가 미상분은 매출도 손익 밖이라 **어떤 경우에도** 차감 대상이 아니다.
    expect((await ladderLine("원가 미상 옵션")).textContent).not.toContain("−");
    // 판매행 없는 옵션분은 included=false이므로 이 창에서는 부호가 없다.
    expect((await ladderLine("그 기간 판매 없는 옵션")).textContent).not.toContain("−");
  });

  it("M2 팔린 옵션의 «판매 없는 날» 광고비가 줄로 보인다 (근거화면 A7이 고발하던 돈)", async () => {
    mount(withPnl({ ad_no_sales_days: "435916", ad_unattributed: "568898" }));
    const row = await ladderLine("판매 없는 날");
    expect(within(row).getByText("435,916원")).toBeTruthy();
  });

  it("금액이 0인 통은 줄을 만들지 않는다 — 빈 줄이 늘어나면 진짜 결손이 묻힌다", async () => {
    mount(BASE);                                   // ad_no_sales_days = "0"
    expect(screen.queryByText(leafText("판매 없는 날"))).toBeNull();
  });

  it("M4 basis='full'이면 두 통이 «−»로 차감되고 사다리 광고비 = 원장 전액이다", async () => {
    mount(withPnl({
      basis: "full", ad_spend: "797431", ad_uncosted: "0",
      ad_no_sales_days: "33750", ad_no_sales_included: true,
      ad_unattributed: "132982", cost_coverage: "1.0000",
    }, { skus: 0, actionable_skus: 0, link_missing_skus: 0, top: [] }));
    expect((await ladderLine("판매 없는 날")).textContent).toContain("−");
    expect((await ladderLine("그 기간 판매 없는 옵션")).textContent).toContain("−");
    // 구멍1은 0이라 줄 자체가 없다(is_full이면 정의상 0이다).
    expect(screen.queryByText(leafText("원가 미상 옵션"))).toBeNull();
  });

  it("M5 각주가 네 통을 **전부** 부르고 손익 밖 합계를 숫자로 말한다", async () => {
    // ★★모든 통이 **0이 아닌** 창으로 본다(적대 리뷰 F-M2 생존분). BASE는
    //   `ad_no_sales_days=0`이라 그 항이 애초에 렌더되지 않아, 각주에서 그 항을 지워도
    //   테스트가 통과했다 — 0으로만 검사하는 통과는 아무것도 안 지킨다(교훈 #181).
    mount(withPnl({ ad_no_sales_days: "435916", ad_unattributed: "568898" }));
    const text = (await footnote()).textContent ?? "";
    for (const s of ["797,431원", "664,449원", "33,750원", "435,916원", "99,232원", "568,898원"]) {
      expect(text, `각주에 ${s}가 없다`).toContain(s);
    }
    // ★계정 총액과의 차는 «결손»이 아니라 **정의 차이**라고 말한다(오독하면 수집 장애로 읽힌다).
    expect(text).toContain("정의 차이");
    // ★그 차는 백엔드가 Decimal로 낸 값을 쓴다 — 화면이 JS float로 다시 빼지 않는다.
    expect(text).toContain("190원");
  });

  it("M-P1-2 included=true면 각주가 «손익 밖»에 이미 차감된 통을 세지 않는다", async () => {
    // 합격기준이 달성된 상태(커버리지 100%) — 여기서 예전 각주는
    // 「원장 797,431 = 손익 797,431」이라 말하면서 동시에 「손익 밖 99,232원」이라 말했다.
    mount(withPnl({
      basis: "full", ad_spend: "797431", ad_uncosted: "0",
      ad_no_sales_days: "33750", ad_no_sales_included: true,
      ad_unattributed: "132982", cost_coverage: "1.0000",
    }, { skus: 0, actionable_skus: 0, link_missing_skus: 0, top: [] }));
    const text = (await footnote()).textContent ?? "";
    expect(text).toContain("손익 밖에 남은 광고비는 없습니다");
    // ★이미 차감된 132,982원을 «남은 합»으로 다시 부르면 같은 돈을 두 번 세는 것이다.
    expect(text).not.toContain("손익 밖에 남은 합이 132,982원");
    // 대신 «그 안에 포함»으로 말한다 — 통이 사라지는 것도 아니다.
    expect(text).toContain("그 안에");
    expect(text).toContain("33,750원");
  });

  it("M-P1-2b included=true인데 구멍1이 남아 있으면 «남은 합»은 그것만이다", async () => {
    /* ★엔진에서는 `is_full ⟹ ad_uncosted=0`이 성립하지만 **화면은 그 불변식을 가정하면
       안 된다** — 옵션 하나가 날마다 다른 sku_id로 관측되면(가격 있는 날/없는 날) 이론상
       included=true와 ad_uncosted>0이 함께 올 수 있다. 그때 `ad_unattributed`를 그대로
       «남은 합»이라 부르면 이미 차감된 두 통까지 다시 세게 된다. 화면은 타입이 허용하는
       모든 응답에서 옳아야 한다. */
    mount(withPnl({
      basis: "full", ad_spend: "760000", ad_uncosted: "37431",
      ad_no_sales_days: "33750", ad_no_sales_included: true,
      ad_unattributed: "170413",
    }));
    const text = (await footnote()).textContent ?? "";
    expect(text).toContain("손익 밖에 남은 합이 37,431원");     // 구멍1만
    expect(text).not.toContain("170,413원");                   // 이미 차감된 것까지 세지 않는다
    // ★남은 돈이 구멍1이면 「귀속할 판매가 없는 돈만」은 거짓이다(2R 트리아지).
    expect(text).not.toContain("귀속할 판매가 없는 돈만");
    expect(text).toContain("원가 미상이라 빠진 것");
  });

  it("남은 돈에 구멍1이 없을 때만 «귀속할 판매가 없는 돈만»이라고 말한다", async () => {
    mount(withPnl({
      basis: "full", ad_uncosted: "0", ad_no_sales_days: "0",
      ad_no_sales: "99232", ad_unattributed: "99232", ad_no_sales_included: false,
    }));
    expect((await footnote()).textContent ?? "").toContain("귀속할 판매가 없는 돈만");
  });

  it("F-M4 차감 대상이 아닌 통에는 «위 손익에 미포함» 경고가 붙어 있다", async () => {
    // ★이 라벨이 금지선(부분집합 매출에서 전량 비용 빼기 금지)의 유일한 화면 경고다.
    mount(BASE);
    expect((await ladderLine("원가 미상 옵션")).textContent).toContain("위 손익에 미포함");
    expect((await ladderLine("그 기간 판매 없는 옵션")).textContent).toContain("위 손익에 미포함");
  });
});

describe("일별 손익 각주 — 광고비 열이 무엇을 빼놓았는지 말한다", () => {
  const DAILY: R["daily"] = [{
    date: "2026-08-08", qty: 210, consumer_revenue: "3817270", our_revenue: "2318513",
    ad_spend_all: "797431", pnl_revenue: "2198728", pnl_qty: 200, cost: "863821.70",
    promo_burden: "195000", ad_spend: "664449",
    ad_no_sales: "99232", ad_no_sales_days: "0", ad_uncosted: "33750",
    vat: "43223.40", net_profit: "432233.90", profit_rate: "0.1966",
    cost_coverage: "0.9483",
  }];

  it("F-M6 세 통 중 하나라도 0이 아니면 각주가 뜨고 셋을 다 부른다", async () => {
    mount({ ...BASE, daily: DAILY });
    const note = await screen.findByText(
      (_t, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes("날짜에 못 붙는"));
    const text = note.textContent ?? "";
    for (const s of ["132,982원", "33,750원", "99,232원"]) {
      expect(text, `일별 각주에 ${s}가 없다`).toContain(s);
    }
  });

  it("F-M6 구멍3이 0이어도 나머지 두 통이 있으면 각주가 뜬다", async () => {
    // ★조건이 `ad_no_sales`만 보면 이 창에서 각주가 통째로 사라진다 — 그런데 여기엔
    //   손익 밖 광고비가 469,666원이나 있다. «셋 중 하나라도»가 조건이라야 한다.
    mount({
      ...withPnl({ ad_no_sales: "0", ad_no_sales_days: "435916", ad_unattributed: "469666" }),
      daily: [{ ...DAILY[0], ad_no_sales: "0", ad_no_sales_days: "435916" }],
    });
    const note = await screen.findByText(
      (_t, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes("날짜에 못 붙는"));
    expect(note.textContent ?? "").toContain("435,916원");
  });

  it("basis='full'이면 «위 손익에 섞지 않았다»고 말하지 않는다 — 이미 차감됐다", async () => {
    /* ★사다리의 «−» 줄과 정면으로 어긋나던 문구. P1-2와 같은 종류인데 이쪽은 일별 각주라
       basis='full'이 라이브에서 도달 가능해진 2026-08-10에야 드러났다. */
    mount({
      ...withPnl({
        basis: "full", ad_spend: "6092627", ad_uncosted: "0",
        ad_no_sales_days: "345359", ad_no_sales: "334445",
        ad_unattributed: "679804", ad_no_sales_included: true, cost_coverage: "1.0000",
      }, { skus: 0, actionable_skus: 0, link_missing_skus: 0, top: [] }),
      daily: [{ ...DAILY[0], ad_uncosted: "0", ad_no_sales_days: "345359", ad_no_sales: "334445" }],
    });
    const note = await screen.findByText(
      (_t, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes("날짜에 못 붙는"));
    const text = note.textContent ?? "";
    expect(text).toContain("기간 사다리 순이익에는 이미 차감돼 있고");
    expect(text).not.toContain("위 손익에 섞지 않았지만");
    expect(text).toContain("679,804원");           // 크기는 계속 보인다
  });

  it("basis='costed_subset'이면 «위 손익에 섞지 않았다»가 참이라 그대로 말한다", async () => {
    mount({ ...BASE, daily: DAILY });             // included=false
    const note = await screen.findByText(
      (_t, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes("날짜에 못 붙는"));
    expect(note.textContent ?? "").toContain("위 손익에 섞지 않았지만");
  });

  it("세 통이 전부 0이면 각주를 띄우지 않는다 — 없는 경고는 진짜 경고를 묻는다", async () => {
    mount({
      ...withPnl({ ad_uncosted: "0", ad_no_sales: "0", ad_unattributed: "0" }),
      daily: [{ ...DAILY[0], ad_no_sales: "0", ad_uncosted: "0", ad_no_sales_days: "0" }],
    });
    await screen.findByText("일별 손익 (1일)");
    expect(screen.queryByText((t) => t.includes("날짜에 못 붙는"))).toBeNull();
  });
});

describe("「원가 제외」 — 결정과 매칭 실패를 가른다", () => {
  const EXCLUDED = withPnl({}, {
    excluded_skus: 2, excluded_our_revenue: "57400",
    excluded_top: [
      {
        sku_id: "37350957", product_name: "Z폴드5 필름 세트", qty: 3,
        our_revenue: "36000", consumer_revenue: "54900", loss_confirmed: false,
        reason: "excluded", is_new: false, first_sold_at: null, first_po_at: null,
        first_sold_at_bounded: false, excluded_note: "no suggestion or low score",
      },
      {
        sku_id: "99999999", product_name: "증정용 샘플", qty: 1,
        our_revenue: "21400", consumer_revenue: "32600", loss_confirmed: false,
        reason: "excluded", is_new: false, first_sold_at: null, first_po_at: null,
        first_sold_at_bounded: false, excluded_note: null,
      },
    ],
  });

  it("M6 «이미 결정한 것»이라 단정하지 않고 기록된 사유를 그대로 보인다", async () => {
    mount(EXCLUDED);
    // ★설명 문단에도 같은 문자열이 있으므로 **그 SKU 행 안에서** 찾는다 — 문단만 있고 행에는
    //   안 찍히면 사용자는 어느 SKU가 매칭 실패인지 끝내 모른다.
    const row = (await screen.findByText(leafText("Z폴드5 필름 세트"))).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("no suggestion or low score")).toBeTruthy();
    // ★prod 22건이 전부 매칭 실패였다 — 화면이 그걸 «결정»이라 부르면 정상 상품이 영영 밖에 남는다.
    expect(screen.queryByText((t) => t.includes("원가를 안 붙이기로 이미 결정한 것"))).toBeNull();
  });

  it("M7 사유가 없으면 빈 칸이 아니라 «기록 없음»이라고 쓴다", async () => {
    mount(EXCLUDED);
    expect(await screen.findByText(leafText("사유 기록 없음"))).toBeTruthy();
  });
});
