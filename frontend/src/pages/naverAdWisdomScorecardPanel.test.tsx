// @vitest-environment jsdom
//
// naverAdWisdomScorecardPanel.test.tsx — M3-a 지혜 성적표 «표면» 회귀 (적대 리뷰 1R, 2026-08-22).
//
// ★존재 이유: 이 패널을 처음 낼 때 백엔드는 HTTP body까지 잘 지켜져 있었는데(라우터에서
//   판정 키를 떨어뜨리는 변이는 죽었다), **body가 화면에 닿는 마지막 한 칸**은 무엇을
//   지워도 프론트 495개 테스트가 전부 초록이었다. 적대 리뷰가 주입한 표면 변이 3종
//   (evidence_gap 렌더 제거 · setWisdomCard 제거 · 귀속 문구 제거)이 **전부 생존**했다.
//
//   이 패널이 존재하는 이유가 「표본이 0일 때 아무것도 안 그리면 «문제없음»으로 읽힌다」인데,
//   그 문구를 지워도 아무도 몰랐다면 패널은 있으나 마나다. 그래서 이 파일은 «값»이 아니라
//   «사람 눈에 닿는가»를 잰다.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => {
  const pending = () => new Promise<never>(() => {});
  return {
    pending,
    wisdom: null as unknown,
    wisdomFails: false,
    avaFails: false,
  };
});

vi.mock("../lib/api", () => ({
  fetchNaverAdReport: () => h.pending(),
  fetchNaverAdProposals: () => h.pending(),
  fetchNaverCampaignSettings: () => h.pending(),
  putNaverCampaignSettings: () => h.pending(),
  fetchNaverAdDiagnosis: () => h.pending(),
  fetchNaverExpertReviews: () =>
    h.avaFails ? Promise.reject(new Error("Ava 조회 실패")) : Promise.resolve({ rows: [] }),
  fetchNaverExpertScorecard: () =>
    h.avaFails
      ? Promise.reject(new Error("Ava 조회 실패"))
      : Promise.resolve({ sample_n: 0, accuracy: null, label: "표본 없음" }),
  fetchNaverWisdomScorecard: () =>
    h.wisdomFails ? Promise.reject(new Error("지혜 조회 실패")) : Promise.resolve(h.wisdom),
  updateNaverProposalStatus: () => h.pending(),
  executeNaverProposal: () => h.pending(),
  getNaverExpertDelegation: () => h.pending(),
  putNaverExpertDelegation: () => h.pending(),
  getNaverDashboardOverview: () => h.pending(),
  getNaverGuardrailParams: () => h.pending(),
  putNaverGuardrailParams: () => h.pending(),
}));

import NaverAdOptimizationConsole from "./NaverAdOptimizationConsole";

const renderPage = () =>
  render(
    <MemoryRouter>
      <NaverAdOptimizationConsole />
    </MemoryRouter>,
  );

const VALUE_DEF = {
  metric: "총이익(gross profit) 절대액",
  formula: "(conv_amt x cf / bep_roas) - cost",
  grain: "조치 1건 (naver_change_log 행)",
  verdict_rule: "조치 전/후 총이익의 부호 비교",
  conversion_delay: { window: "D+1~D+7 (전환 정착 창)", correction_applied: false, note: "미적용" },
  bep_coverage: { groups_total: 1013, groups_with_product_bep: 231, ratio: 0.228, note: "근사" },
  legacy_note: "옛 자는 불변 보존",
};

const ATTRIBUTION = {
  path: "OpsWisdomEntry.param_proposal_id -> NaverProposal -> NaverChangeLog",
  limitation: "추적 가능한 경로는 param_proposal_id 1:1 링크뿐이다. 이 롤업은 지혜 기여의 하한이다.",
};

const ROW_BASE = {
  wisdom_id: 1,
  wisdom_text: "주말·여름·아이폰 비시즌 조건에서 bid_up은 차단한다.",
  status: "active",
  promoted_at: "2026-07-27 08:45:00",
  source_candidate_id: 3,
  linked_proposals: [],
  linked_proposal_count: 1,
  has_evidence: false,
  evidence_gap: "제안은 났으나 실집행 조치가 0건이다 (제안 상태: rejected).",
  changes_total: 0,
  changes_executed: 0,
  changes_scored_profit: 0,
  verdicts: {},
  bep_sources: {},
  gave_before_sum: null,
  gave_after_sum: null,
  gave_delta_sum: null,
  gave_pairs: 0,
  profit_before_sum: null,
  profit_after_sum: null,
  profit_delta_sum: null,
  profit_pairs: 0,
  profit_unavailable: 0,
  details: [],
};

const card = (row: Record<string, unknown>) => ({
  generated_at_kst: "2026-08-22 18:00:00",
  wisdom_total: 1,
  wisdom_active: 1,
  wisdom_with_evidence: row.has_evidence ? 1 : 0,
  value_definition: VALUE_DEF,
  attribution: ATTRIBUTION,
  wisdom: [row],
});

afterEach(() => {
  cleanup();
  h.wisdom = null;
  h.wisdomFails = false;
  h.avaFails = false;
  vi.clearAllMocks();
});

describe("지혜 성적표 패널 — 사람 눈에 닿는가", () => {
  it("표본 0이면 «왜 잴 것이 없나»를 화면에 낸다 (빈 성적표를 «문제없음»으로 읽지 않게)", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/아직 잴 것이 없습니다/)).toBeTruthy();
    expect(screen.getByText(/실집행 조치가 0건/)).toBeTruthy();
  });

  it("귀속의 «한계»가 화면에 남는다 (롤업이 하한이라는 사실이 숫자 옆에 있어야 한다)", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/지혜 기여의 하한/)).toBeTruthy();
  });

  it("값의 정의(식·정착보정 상태·BEP 커버리지)가 산출물 옆에 붙는다", async () => {
    h.wisdom = card(ROW_BASE);
    renderPage();
    expect(await screen.findByText(/conv_amt x cf \/ bep_roas/)).toBeTruthy();
    expect(screen.getByText(/정착 보정 미적용/)).toBeTruthy();
    expect(screen.getByText(/231\/1013/)).toBeTruthy();
  });

  it("★크기 축은 총이익 «금액»이다 — GAVE가 헤드라인이 되면 판정과 반대 부호를 가리킬 수 있다", async () => {
    h.wisdom = card({
      ...ROW_BASE,
      has_evidence: true,
      changes_total: 1,
      changes_executed: 1,
      changes_scored_profit: 1,
      verdicts: { declined: 1 },
      bep_sources: { product_bep: 1 },
      gave_delta_sum: 250000,
      gave_pairs: 1,
      profit_delta_sum: -533333,
      profit_pairs: 1,
      evidence_gap: null,
    });
    renderPage();
    // 총이익 금액이 «있어야» 한다
    expect(await screen.findByText(/총이익 델타 -533,333원/)).toBeTruthy();
    // GAVE는 남되 «참고»로 강등돼야 한다
    expect(screen.getByText(/참고 GAVE 델타/)).toBeTruthy();
    expect(screen.getByText(/총이익 악화 1건/)).toBeTruthy();
  });

  it("금액 산출불가 건수가 숨지 않는다 (렌즈 미기록을 0원으로 읽지 않게)", async () => {
    h.wisdom = card({
      ...ROW_BASE, has_evidence: true, changes_total: 2, changes_executed: 2,
      changes_scored_profit: 2, verdicts: { improved: 2 }, profit_unavailable: 2,
      evidence_gap: null,
    });
    renderPage();
    expect(await screen.findByText(/금액 산출불가 2건/)).toBeTruthy();
  });

  it("BEP 커버리지 산출이 실패해도 그 사실이 화면에 남는다", async () => {
    h.wisdom = card(ROW_BASE);
    (h.wisdom as any).value_definition = {
      ...VALUE_DEF,
      bep_coverage: { groups_total: null, groups_with_product_bep: null, ratio: null,
                      note: "커버리지 산출에 실패했다(판정불능)" },
    };
    renderPage();
    expect(await screen.findByText(/판정불능/)).toBeTruthy();
  });

  it("★적대 리뷰 P1-2: Ava 조회가 실패해도 지혜 성적표는 화면에 남는다", async () => {
    h.avaFails = true;
    h.wisdom = card(ROW_BASE);
    renderPage();
    // 지혜 응답은 이미 성공했다 — 옆 패널의 장애가 이걸 «조용한 빈 카드»로 만들면 안 된다.
    expect(await screen.findByText(/아직 잴 것이 없습니다/)).toBeTruthy();
  });

  it("지혜 조회 자체가 실패하면 그 사실을 말한다 (조용히 비어 있지 않게)", async () => {
    h.wisdomFails = true;
    renderPage();
    await waitFor(() => expect(screen.getByText(/지혜 조회 실패/)).toBeTruthy());
  });
});
