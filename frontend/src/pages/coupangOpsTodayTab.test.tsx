// @vitest-environment jsdom
//
// coupangOpsTodayTab.test.tsx — 「오늘」 탭에서도 뺀 1P 광고비가 화면에 남는가.
//
// 적대 리뷰 1R P1-1: 판매유형 표(각주 포함)가 `ad_ref_date ? (오늘) : (그 외)` 삼항의
//   **else 가지에만** 있었다. `ad_ref_date`가 non-null인 조건이 곧 days=0이므로,
//   **1P를 빼는 바로 그 탭에서 각주가 원리적으로 안 그려졌다.**
//   컴포넌트 단위 테스트는 이 구멍을 못 본다 — 컴포넌트는 멀쩡하고 «안 부른 것»이 문제라서다.
//   그래서 페이지를 통째로 그려서 확인한다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { SalesSummary } from "../lib/api";
import { kstDate } from "../lib/periodRange";

const h = vi.hoisted(() => ({
  fetchSalesSummary: vi.fn(),
  getCoupangAdCostDaily: vi.fn(),
  requestAdCostRefresh: vi.fn(),
  getAdCostRefreshStatus: vi.fn(),
}));
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchSalesSummary: h.fetchSalesSummary,
  getCoupangAdCostDaily: h.getCoupangAdCostDaily,
  requestAdCostRefresh: h.requestAdCostRefresh,
  getAdCostRefreshStatus: h.getAdCostRefreshStatus,
}));

import CoupangOps from "./CoupangOps";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

/** 「오늘」 탭 = ad_ref_date 비-null. 라이브 2026-08-12 오하이테크 값. */
const TODAY_RESPONSE: SalesSummary = {
  period: { from: "2026-08-13", to: "2026-08-13" },
  ad_ref_date: "2026-08-12",
  summary: {
    revenue: "53700", fee: "4607", cost: "10441",
    ad_spend: "40361", shipping: "5700",
    profit: "-7410", profit_rate: "-13.80",
    profit_excl_ad: "32952", profit_rate_excl_ad: "61.36",
    conv_revenue: "71600", roas: "1.77",
    excluded_ad_spend: "536212", excluded_ad_conv: "1383850",
    ad_spend_unassigned: "0",
  },
  by_sell_type: [
    { sell_type: "3P", channel_type: "Wing", revenue: "53700", fee: "4607",
      cost: "10441", ad_spend: "40361", shipping: "5700", profit: "-7410",
      profit_rate: "-13.80", conv_revenue: "71600", roas: "1.77" },
    { sell_type: "2P", channel_type: "로켓그로스", revenue: "0", fee: "0",
      cost: "0", ad_spend: "0", shipping: "0", profit: "0",
      profit_rate: null, conv_revenue: "0", roas: null },
  ],
  by_product: [],
};

function renderPage() {
  h.fetchSalesSummary.mockResolvedValue(TODAY_RESPONSE);
  h.getCoupangAdCostDaily.mockResolvedValue([]);
  h.getAdCostRefreshStatus.mockResolvedValue({ requested: false });
  return render(
    <MemoryRouter>
      <CoupangOps />
    </MemoryRouter>
  );
}

describe("쿠팡 운영 패널 · 「오늘」 탭", () => {
  it("뺀 로켓배송(1P) 광고비가 화면에 남는다 — 빼는 탭에서 숨으면 은폐다", async () => {
    renderPage();
    // ★인자를 못박는다(적대 리뷰 1R P1-1). `toHaveBeenCalled()`만 보면 기본 기간이
    //   7일→30일로 바뀌거나 from/to가 뒤바뀌어도 통과한다 — 실제로 변이 3종이 살아남았다.
    await waitFor(() =>
      expect(h.fetchSalesSummary).toHaveBeenCalledWith("ALL", 0, kstDate(-6), kstDate(0)));
    await waitFor(() => {
      expect(screen.getByText(/로켓배송\(1P\) 광고비/)).toBeTruthy();
    });
    expect(screen.getByText("536,212원")).toBeTruthy();
  });

  it("판매유형 표가 이 탭에도 그려진다", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("3P · Wing")).toBeTruthy();
    });
    expect(screen.getByText("2P · 로켓그로스")).toBeTruthy();
  });
});
