// @vitest-environment jsdom
//
// naverAdGuardrailWindowCoverage.test.tsx — 안전 봉투 「창 재료 커버리지」 표면
// (D-NAO-262 #14, 적대 리뷰 P2-1 채택분).
//
// ## 왜 이 테스트가 있어야 하나
// 백엔드 `guardrail_params_window_coverage()`는 「창 파라미터를 봉투 상한까지 끝까지
// 늘렸을 때, 그만큼의 원본 데이터가 실제로 있는가」를 계산해 `window_coverage`로 내려
// 보낸다. 값은 계산되는데 화면에 안 그려지면 결손이 조용히 났을 때(수집이 며칠 죽는
// 경우) 아무도 모른다 — 이 저장소가 같은 모양의 결함(#362 「만드는 층과 닿는 층은
// 다른 층이다」)에 이미 세 번 데였다. 그래서 이 테스트는 «값이 존재한다»가 아니라
// «사람이 읽는 텍스트에 그 사실이 있는가»를 잰다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return { pending, guardrail: null as unknown };
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
  getNaverGuardrailParams: () => (h.guardrail ? Promise.resolve(h.guardrail) : h.pending()),
  putNaverGuardrailParams: () => h.pending(),
}));

import NaverAdOptimizationConsole from "./NaverAdOptimizationConsole";

const renderPage = () =>
  render(
    <MemoryRouter>
      <NaverAdOptimizationConsole />
    </MemoryRouter>,
  );

// 응답 골격 — params/from_db_enabled/from_db_help/retro_freshness는 이 화면의 다른
// 관심사라 이 테스트에선 최소값으로만 채운다. 재는 것은 window_coverage뿐이다.
const RESPONSE_BASE = {
  params: [],
  from_db_enabled: true,
  from_db_help: "되돌림 절차 설명",
  retro_freshness: { latest_asof: "2026-08-26", expected_asof: "2026-08-26", stale: false, lag_days: 0 },
};

afterEach(() => {
  cleanup();
  h.guardrail = null;
  vi.clearAllMocks();
});

describe("안전 봉투 — 창 재료 커버리지(D-NAO-262 #14)", () => {
  it("결손이 있는 행은 결손임이 눈에 보이게 화면에 뜬다 (일수·최신일자 포함)", async () => {
    h.guardrail = {
      ...RESPONSE_BASE,
      window_coverage: [
        {
          param_key: "pl_window_days", promoted: true, source: "expkeyword",
          label: "파워링크 제외 판정", ceiling_days: 90, latest: "2026-08-20",
          window_from: "2026-05-23", missing_days: 12, covered: false, note: null,
        },
      ],
    };
    renderPage();
    expect(await screen.findByText(/파워링크 제외 판정/)).toBeTruthy();
    // 「필드가 존재한다」가 아니라 결손 사실 자체가 텍스트로 있어야 한다.
    expect(screen.getByText(/결손 12일/)).toBeTruthy();
    expect(screen.getByText(/2026-08-20/)).toBeTruthy();
  });

  it("covered:true만 있으면 결손 문구 없이 정상으로 뜬다", async () => {
    h.guardrail = {
      ...RESPONSE_BASE,
      window_coverage: [
        {
          param_key: "pl_window_days", promoted: true, source: "expkeyword",
          label: "파워링크 제외 판정", ceiling_days: 90, latest: "2026-08-26",
          window_from: "2026-05-29", missing_days: 0, covered: true, note: null,
        },
      ],
    };
    renderPage();
    expect(await screen.findByText(/재료 충족/)).toBeTruthy();
    expect(screen.queryByText(/결손/)).toBeNull();
  });

  it("promoted:false 행은 «승격 보류»로 보인다 (봉투 없음과 재료 없음은 다른 사실)", async () => {
    h.guardrail = {
      ...RESPONSE_BASE,
      window_coverage: [
        {
          param_key: null, promoted: false, source: "shopping",
          label: "쇼핑 제외 판정", ceiling_days: 14, latest: "2026-08-26",
          window_from: "2026-08-13", missing_days: 0, covered: true, note: null,
        },
      ],
    };
    renderPage();
    expect(await screen.findByText(/쇼핑 제외 판정/)).toBeTruthy();
    expect(screen.getByText(/승격 보류/)).toBeTruthy();
  });

  it("원본 0행(latest=null)이면 note를 그대로 보여준다", async () => {
    h.guardrail = {
      ...RESPONSE_BASE,
      window_coverage: [
        {
          param_key: null, promoted: false, source: "shopping",
          label: "쇼핑 제외 판정", ceiling_days: 14, latest: null,
          missing_days: null, covered: false, note: "원본 0행 — 창을 못 세운다",
        },
      ],
    };
    renderPage();
    expect(await screen.findByText(/원본 0행 — 창을 못 세운다/)).toBeTruthy();
  });

  it("섞인 케이스 — 결손 행과 충족 행이 각각 다르게 뜬다", async () => {
    h.guardrail = {
      ...RESPONSE_BASE,
      window_coverage: [
        {
          param_key: "pl_window_days", promoted: true, source: "expkeyword",
          label: "파워링크 제외 판정", ceiling_days: 90, latest: "2026-08-20",
          window_from: "2026-05-23", missing_days: 12, covered: false, note: null,
        },
        {
          param_key: null, promoted: false, source: "shopping",
          label: "쇼핑 제외 판정", ceiling_days: 14, latest: "2026-08-26",
          window_from: "2026-08-13", missing_days: 0, covered: true, note: null,
        },
      ],
    };
    renderPage();
    expect(await screen.findByText(/결손 12일/)).toBeTruthy();
    expect(screen.getByText(/재료 충족/)).toBeTruthy();
    expect(screen.getByText(/승격 보류/)).toBeTruthy();
  });
});
