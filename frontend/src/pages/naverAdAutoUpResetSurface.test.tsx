// @vitest-environment jsdom
//
// naverAdAutoUpResetSurface.test.tsx — D-NAO-287 「상한리셋 목표」의 **표면** 회귀.
//
// ★존재 이유(계약 §4-D③): 이 저장소가 반복해 데인 결함은 「값은 도는데 사람에게 안 닿는」
//   것이다. 백엔드 테스트 10종이 전부 초록이어도 **버튼의 onClick 한 칸**을 지우면 리셋
//   입구는 사라지는데 아무도 모른다 — 그러면 상한은 여전히 「사람 개입으로만 리셋」인데
//   개입할 입구가 없는 원래 상태로 조용히 돌아간다.
//   그래서 이 파일은 «값»이 아니라 «사람 눈에 닿고 손이 가 닿는가»를 잰다.
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    ceiling: null as unknown,
    resetCalls: [] as unknown[],
    resetResult: null as unknown,
  };
});

vi.mock("../lib/api", () => ({
  fetchNaverAdReport: () => h.pending(),
  fetchNaverAdProposals: () => h.pending(),
  fetchNaverCampaignSettings: () => h.pending(),
  putNaverCampaignSettings: () => h.pending(),
  fetchNaverAdDiagnosis: () => h.pending(),
  fetchNaverExpertReviews: () => Promise.resolve({ rows: [] }),
  fetchNaverExpertScorecard: () =>
    Promise.resolve({ sample_n: 0, accuracy: null, label: "표본 없음" }),
  fetchNaverWisdomScorecard: () => h.pending(),
  updateNaverProposalStatus: () => h.pending(),
  executeNaverProposal: () => h.pending(),
  getNaverExpertDelegation: () => h.pending(),
  putNaverExpertDelegation: () => h.pending(),
  getNaverDashboardOverview: () => h.pending(),
  getNaverGuardrailParams: () => h.pending(),
  putNaverGuardrailParams: () => h.pending(),
  fetchNaverProposableTypes: () => h.pending(),
  createNaverProposal: () => h.pending(),
  getNaverAutoUpCeiling: () => (h.ceiling ? Promise.resolve(h.ceiling) : h.pending()),
  resetNaverAutoUpBase: (body: unknown) => {
    h.resetCalls.push(body);
    return h.resetResult ? Promise.resolve(h.resetResult) : h.pending();
  },
}));

import NaverAdOptimizationConsole from "./NaverAdOptimizationConsole";

const AD = "nad-a001-02-000000558104404";

const renderPage = () =>
  render(
    <MemoryRouter>
      <NaverAdOptimizationConsole />
    </MemoryRouter>,
  );

const cappedRow = {
  entity_id: AD,
  campaign_id: "cmp-1",
  base_bid: 1000,
  ceiling: 2000,
  current_bid: 2100,
  current_bid_as_of: "2026-09-05T10:00:00",
  current_bid_source: "last_known",
  headroom_pct: null,
  capped: true,
  cap_applies: true,
};

beforeEach(() => {
  h.ceiling = null;
  h.resetCalls = [];
  h.resetResult = null;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("자동 상향 여력 판 — 표면", () => {
  it("상한 도달 소재가 있으면 그 행과 「기준점 리셋」 버튼이 보인다", async () => {
    h.ceiling = {
      as_of: "2026-09-05T11:00:00", multiple: 2.0, counted: 1,
      cap_applies_count: 1, capped_count: 1, truncated: false, rows: [cappedRow],
    };
    renderPage();
    expect(await screen.findByText("자동 상향 여력")).toBeTruthy();
    expect(await screen.findByText(AD)).toBeTruthy();
    expect(screen.getByText("상한 도달")).toBeTruthy();
    expect(screen.getByRole("button", { name: "기준점 리셋" })).toBeTruthy();
  });

  it("★버튼 클릭이 resetNaverAutoUpBase에 «사유와 함께» 닿는다 (onClick 절단 변이 표적)", async () => {
    h.ceiling = {
      as_of: "2026-09-05T11:00:00", multiple: 2.0, counted: 1,
      cap_applies_count: 1, capped_count: 1, truncated: false, rows: [cappedRow],
    };
    h.resetResult = {
      entity_id: AD, actor: "console", reason: "굳은 소재 복귀", changed_at: "2026-09-05T11:30:00",
      multiple: 2.0, base_before: 1000, base_after: 2100,
      ceiling_before: 2000, ceiling_after: 4200, live_bid: 2100,
      side_effect: { cooldown_hours: 2, changes_today: 2 },
    };
    vi.spyOn(window, "prompt").mockReturnValue("굳은 소재 복귀");

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "기준점 리셋" }));

    await waitFor(() => expect(h.resetCalls.length).toBe(1));
    expect(h.resetCalls[0]).toEqual({ entityId: AD, reason: "굳은 소재 복귀" });
  });

  it("★리셋 결과가 «부작용까지» 화면에 뜬다 — 쿨다운을 말하지 않으면 다시 상한 탓으로 오독된다", async () => {
    h.ceiling = {
      as_of: "2026-09-05T11:00:00", multiple: 2.0, counted: 1,
      cap_applies_count: 1, capped_count: 1, truncated: false, rows: [cappedRow],
    };
    h.resetResult = {
      entity_id: AD, actor: "console", reason: "복귀", changed_at: "2026-09-05T11:30:00",
      multiple: 2.0, base_before: 1000, base_after: 2100,
      ceiling_before: 2000, ceiling_after: 4200, live_bid: 2100,
      side_effect: { cooldown_hours: 2, changes_today: 2 },
    };
    vi.spyOn(window, "prompt").mockReturnValue("복귀");

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "기준점 리셋" }));

    const notice = await screen.findByText(/기준점 1000원 → 2100원/);
    expect(notice.textContent).toContain("쿨다운 2시간");
    expect(notice.textContent).toContain("오늘 변경 2건");
  });

  it("사유를 비우면 부르지 않는다 — 이 입구의 목적이 감사 기록이다", async () => {
    h.ceiling = {
      as_of: "2026-09-05T11:00:00", multiple: 2.0, counted: 1,
      cap_applies_count: 1, capped_count: 1, truncated: false, rows: [cappedRow],
    };
    vi.spyOn(window, "prompt").mockReturnValue("   ");
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "기준점 리셋" }));
    expect(await screen.findByText(/사유가 비어 있어 리셋하지 않았습니다/)).toBeTruthy();
    expect(h.resetCalls.length).toBe(0);
  });

  it("★상한에 닿은 소재가 0개면 «0개»라고 말한다 — 빈 표로 두지 않는다", async () => {
    h.ceiling = {
      as_of: "2026-09-05T11:00:00", multiple: 2.0, counted: 1,
      cap_applies_count: 1, capped_count: 0, truncated: false,
      rows: [{ ...cappedRow, current_bid: 1400, capped: false, headroom_pct: 42.9 }],
    };
    renderPage();
    expect(await screen.findByText(/지금 상한에 닿은 소재는 0개입니다/)).toBeTruthy();
    expect(screen.getByText("+42.9%")).toBeTruthy();
  });

  it("상한이 적용되는 소재 자체가 없으면 그것도 «대상 없음»으로 구분해 말한다", async () => {
    h.ceiling = {
      as_of: "2026-09-05T11:00:00", multiple: 2.0, counted: 3,
      cap_applies_count: 0, capped_count: 0, truncated: false, rows: [],
    };
    renderPage();
    expect(await screen.findByText(/누적 상한이 적용되는 소재가 없습니다/)).toBeTruthy();
  });
});
