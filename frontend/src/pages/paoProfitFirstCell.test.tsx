// @vitest-environment jsdom
//
// paoProfitFirstCell.test.tsx — 성과 화면 **첫 칸이 총이익인지** 잰다 (설계서 122 §4-1).
//
// ## 왜 이 파일이 있나
//
// §4-1이 총이익을 첫 칸으로 올리라고 한 것은 배치 취향이 아니다 — *"첫 칸이 ROAS면 화면이
// 그 표류를 다시 유도한다"*(D-NAO-85 실측: ROAS +7% · 매출 −52%). 그러니 「첫 칸」은
// 값이 응답에 실려 있는지가 아니라 **사람이 맨 처음 보는 자리에 있는지**가 계약이다.
// 서버가 값을 계속 실어 보내도 렌더에서 빼면 그 계약은 조용히 깨진다.
//
// ⇒ 재는 것 셋: ①첫 칸의 라벨이 총이익이고 광고비보다 **앞**에 온다 ②모르면 0이 아니라
//   「모름」이라 말한다 ③합계가 **몇 개 위에서** 잰 값이고 어느 매출 기준인지 말한다.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { NaverPerformanceDay } from "../lib/api";

const h = vi.hoisted(() => ({ pending: () => new Promise<never>(() => {}) }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchNaverPerformanceCampaignOptions: () => Promise.resolve([]),
  fetchNaverOwnershipCampaigns: () => h.pending(),
  fetchNaverOwnershipBands: () => h.pending(),
  fetchNaverPerformanceTimeline: () => h.pending(),
  fetchNaverPerformanceBudget: () => h.pending(),
  fetchNaverPerformanceCompare: () => h.pending(),
  fetchNaverPerformanceBepBreakdown: () => h.pending(),
  fetchNaverPerformanceCampaign: () => h.pending(),
  fetchNaverPerformanceDay: () => Promise.resolve(DAY),
}));

import NaverAdPerformance from "./NaverAdPerformance";

const DAY: NaverPerformanceDay = {
  as_of: "2026-09-04T12:00:00",
  date: "2026-09-04",
  data_note: "출처 안내",
  campaigns: [],
  totals: {
    gross_profit_today: -41300,
    gross_profit_known_campaigns: 1,
    gross_profit_unknown_campaigns: 1,
    gross_profit_basis: "오늘 추정",
    gross_profit_lens_note:
      "보정 전 값입니다 — 다른 화면의 총이익은 보정계수를 적용해 값이 다를 수 있습니다.",
    spend_today: 46300,
    campaigns_active_today: 2,
    campaigns_total: 2,
  },
  today_actions: {
    executed_count: 0, blocked_count: 0, unknown_count: 0, items: [], quiet_reason: "조용합니다",
  },
  is_today: true,
  source: "today_proxy",
  source_label: "오늘 추정",
  campaign_filter: null,
  data_gap_note: null,
};

afterEach(cleanup);

async function renderScreen() {
  render(<MemoryRouter><NaverAdPerformance /></MemoryRouter>);
  // ★/총이익/으로 기다리면 「총이익을 계산할 수 없습니다」 문구까지 걸려 여러 개가 잡힌다.
  //   칸의 «값»이 그려질 때까지 기다린다.
  await waitFor(() => expect(screen.getByText(/매출 기준/)).toBeTruthy());
}

describe("총이익이 첫 칸이다", () => {
  it("① 총이익 칸이 광고비 칸보다 앞에 온다", async () => {
    await renderScreen();
    const labels = Array.from(document.querySelectorAll(".text-xs.text-gray-500"))
      .map((e) => e.textContent ?? "");
    const profit = labels.findIndex((t) => t.trim().endsWith("총이익"));
    const spend = labels.findIndex((t) => t.includes("쓴 광고비"));
    expect(profit).toBeGreaterThanOrEqual(0);
    expect(spend).toBeGreaterThan(profit);   // ★순서가 계약이다
    // ★첫 칸은 **금액**이라 저장소 공용 `won()`을 그대로 쓴다(음수 부호도 그것이 정한다).
    //   「수정 사항」의 결과 칸이 「+N원/−N원」으로 부호를 명시하는 것과 다른 이유: 그쪽은
    //   **델타**라 §4-3이 부호를 요구하고, 이쪽은 그날의 총액이다.
    expect(await screen.findByText("-41,300원")).toBeTruthy();
  });

  it("② 합계가 몇 개 위에서 잰 값이고 어느 매출 기준인지 말한다", async () => {
    await renderScreen();
    const sub = screen.getByText(/오늘 추정 매출 기준/);
    expect(sub.textContent).toContain("광고 1개");
    // ★뺀 것을 숨기지 않는다 — 모르는 캠페인을 0으로 셌으면 이 문장이 필요 없었다.
    expect(sub.textContent).toContain("뺀 광고 1개(집행 없음·BEP 모름)");
    // ★이 값이 **보정 전**이라는 자백이 같은 줄에 있다 — 없으면 다른 화면의 총이익과
    //   값이 다른데 「같은 값」으로 읽힌다(적대 리뷰 P1-2).
    expect(sub.textContent).toContain("보정 전 값입니다");
  });

  it("③ 하나도 모르면 0이 아니라 「모름」이라 말한다", async () => {
    DAY.totals.gross_profit_today = null;
    DAY.totals.gross_profit_known_campaigns = 0;
    try {
      await renderScreen();
      expect(await screen.findByText("모름")).toBeTruthy();
      expect(screen.queryByText("0원")).toBeNull();
      expect(screen.getByText(/총이익을 계산할 수 없습니다/)).toBeTruthy();
    } finally {
      DAY.totals.gross_profit_today = -41300;
      DAY.totals.gross_profit_known_campaigns = 1;
    }
  });

  it("④ 셀 광고가 아예 없으면 「0개 광고 전부…」라 하지 않는다", async () => {
    // ★적대 리뷰 P2-1 — unknown=0인데 값이 없으면 «모르는» 게 아니라 «셀 게 없는» 것이다.
    DAY.totals.gross_profit_today = null;
    DAY.totals.gross_profit_known_campaigns = 0;
    DAY.totals.gross_profit_unknown_campaigns = 0;
    try {
      await renderScreen();
      expect(await screen.findByText("집계할 광고가 없습니다.")).toBeTruthy();
      expect(screen.queryByText(/0개 광고 전부/)).toBeNull();
    } finally {
      DAY.totals.gross_profit_today = -41300;
      DAY.totals.gross_profit_known_campaigns = 1;
      DAY.totals.gross_profit_unknown_campaigns = 1;
    }
  });
});
