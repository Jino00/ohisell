// @vitest-environment jsdom
//
// naverAdProposalFormMounted.test.tsx — 발의 폼이 «콘솔에 실제로 붙어 있는가»
// (D-NAO-283 · 계약 `CONTRACT_pao_purpose_and_hands.md` §6 P2 「콘솔 발의 폼」)
//
// ## 왜 이 파일이 «따로» 있어야 하나 — 교훈 #380의 정면
// `naverAdProposalForm.test.tsx`는 폼 컴포넌트를 **직접 렌더**한다. 그래서 콘솔에서
// `<NaverAdProposalForm />` 한 줄을 지워도 그 파일은 전건 초록이다 — 폼은 완벽히 동작하는데
// **사람이 갈 수 있는 화면에는 없는** 상태가 된다. n=77이 값을 치른 그 병("두 층 각각은
// 지켜지는데 둘을 잇는 한 줄만 아무도 안 지킨다")과 글자 그대로 같은 모양이다.
//
// 계약이 지목한 표면은 「폼 컴포넌트」가 아니라 **「콘솔 발의 폼」**이다. 그러니 그 마운트
// 자체가 하나의 합격 조건이고, 이 파일이 그 한 줄을 지킨다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    types: {
      proposable: [
        { proposal_type: "bid_down", action: "update_bid", direction: "down" },
        { proposal_type: "negative_keyword", action: "add_negative_keyword", direction: null },
      ],
      engine_only: [
        { proposal_type: "bid_up_explore", reason: "이 유형은 엔진만 발의합니다 — 탐색 전용" },
      ],
      open_actions: ["add_negative_keyword", "update_bid"],
    },
  };
});

vi.mock("../lib/api", () => ({
  fetchNaverAdReport: () => h.pending(),
  fetchNaverAdProposals: () => h.pending(),
  fetchNaverCampaignSettings: () => h.pending(),
  putNaverCampaignSettings: () => h.pending(),
  fetchNaverAdDiagnosis: () => h.pending(),
  fetchNaverExpertReviews: () => Promise.resolve({ rows: [] }),
  fetchNaverExpertScorecard: () => Promise.resolve({ sample_n: 0, accuracy: null, label: "표본 없음" }),
  fetchNaverWisdomScorecard: () => h.pending(),
  updateNaverProposalStatus: () => h.pending(),
  executeNaverProposal: () => h.pending(),
  getNaverExpertDelegation: () => h.pending(),
  putNaverExpertDelegation: () => h.pending(),
  getNaverDashboardOverview: () => h.pending(),
  getNaverGuardrailParams: () => h.pending(),
  putNaverGuardrailParams: () => h.pending(),
  fetchNaverProposableTypes: () => Promise.resolve(h.types),
  createNaverProposal: () => h.pending(),
}));

import NaverAdOptimizationConsole from "./NaverAdOptimizationConsole";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("발의 폼이 콘솔에 마운트돼 있다 (D-NAO-283)", () => {
  it("★배선 절단 변이: 콘솔에서 <NaverAdProposalForm/>을 떼면 이 테스트만 빨개진다", async () => {
    render(
      <MemoryRouter>
        <NaverAdOptimizationConsole />
      </MemoryRouter>,
    );

    // 「발의하기」 버튼은 이 폼에만 있다 — 콘솔 어디에도 같은 이름이 없다.
    expect(await screen.findByRole("button", { name: "발의하기" })).toBeTruthy();
  });

  it("발의가 «승인이 아님»을 콘솔 화면에서 말한다 — 오독의 대가가 실쓰기다", async () => {
    render(
      <MemoryRouter>
        <NaverAdOptimizationConsole />
      </MemoryRouter>,
    );

    const note = await screen.findByText(/발의는 승인이 아닙니다/);
    expect(note.textContent).toContain("승인·실행은 지금까지와 똑같이 별도 Confirm");
  });
});
