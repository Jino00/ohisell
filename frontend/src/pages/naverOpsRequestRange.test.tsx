// @vitest-environment jsdom
//
// naverOpsRequestRange.test.tsx — 스마트스토어 패널이 **어느 기간을 요청하는가**.
//
// 적대 리뷰 1R P1-1(2026-08-14): 기간 판정이 백엔드 `days`에서 프론트로 옮겨왔는데
//   그 자리에 테스트가 없어, 기본 기간을 7일→30일로 바꾸는 변이가 살아남았다.
//   화면은 「7일」이 눌린 것처럼 보이면서 30일치 숫자를 보여줄 수 있었다.
//
// 이 파일이 죽이는 변이: 기본 기간 변경 · from/to 인자 뒤바뀜 · 날짜를 안 보내고 days로 회귀.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { kstDate } from "../lib/periodRange";

const h = vi.hoisted(() => ({
  fetchNaverSalesSummary: vi.fn(),
  fetchGfaStatus: vi.fn(),
  fetchNaverSettlementSummary: vi.fn(),
  syncNaverOrders: vi.fn(),
}));
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchNaverSalesSummary: h.fetchNaverSalesSummary,
  fetchGfaStatus: h.fetchGfaStatus,
  fetchNaverSettlementSummary: h.fetchNaverSettlementSummary,
  syncNaverOrders: h.syncNaverOrders,
}));

import NaverOps from "./NaverOps";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const EMPTY = {
  period: { from: "", to: "" },
  ad_ref_date: null,
  ad_basis: null,
  summary: {
    revenue: "0", revenue_vat_incl: "0", product_revenue: "0", delivery_revenue: "0",
    fee: "0", cost: "0", ad_spend: "0", logistics: "0", shipment_count: 0,
    profit: "0", profit_rate: null, supply_basis: true,
  },
  by_product: [],
} as unknown as Awaited<ReturnType<typeof import("../lib/api").fetchNaverSalesSummary>>;

describe("스마트스토어 패널의 기본 조회 기간", () => {
  it("첫 로드는 최근 7일을 **날짜로** 요청한다", async () => {
    h.fetchNaverSalesSummary.mockResolvedValue(EMPTY);
    h.fetchGfaStatus.mockResolvedValue(null);
    h.fetchNaverSettlementSummary.mockResolvedValue(null);
    render(<MemoryRouter><NaverOps /></MemoryRouter>);
    // 종전 백엔드 `days=7`(= today-6 ~ today)과 **같은 창**이어야 회귀가 아니다.
    await waitFor(() =>
      expect(h.fetchNaverSalesSummary).toHaveBeenCalledWith(0, kstDate(-6), kstDate(0)));
  });

  it("90일을 넘는 구간은 **요청 자체가 안 나간다** — 상한 배선까지 본다", async () => {
    // ★적대 리뷰 2R 이월: `customRangeError`의 90일 상한을 테스트가 라이브러리에서만 보고
    //   있어, 화면 호출부에서 `OPS_MAX_SPAN_DAYS` 인자를 지워도 전부 통과했다.
    //   그러면 91일 요청이 나가 백엔드 400 원문이 화면에 새고 표는 옛 숫자를 유지한다.
    //   여기서는 «배선»을 본다: 실제로 화면의 날짜 칸을 바꿔 요청이 안 나가는지.
    h.fetchNaverSalesSummary.mockResolvedValue(EMPTY);
    h.fetchGfaStatus.mockResolvedValue(null);
    h.fetchNaverSettlementSummary.mockResolvedValue(null);
    const { container } = render(<MemoryRouter><NaverOps /></MemoryRouter>);
    await waitFor(() => expect(h.fetchNaverSalesSummary).toHaveBeenCalled());
    const calls = h.fetchNaverSalesSummary.mock.calls.length;

    const [fromInput] = Array.from(
      container.querySelectorAll<HTMLInputElement>('input[type="date"]'));
    expect(fromInput).toBeTruthy();
    fireEvent.change(fromInput, { target: { value: kstDate(-120) } });   // 121일 구간

    await new Promise((r) => setTimeout(r, 50));
    expect(h.fetchNaverSalesSummary.mock.calls.length).toBe(calls);
  });
});
