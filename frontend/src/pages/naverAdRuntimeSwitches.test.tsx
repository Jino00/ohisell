// @vitest-environment jsdom
//
// naverAdRuntimeSwitches.test.tsx — 킬스위치 2종이 «화면에 보이고 화면에서 바뀌는가»
// (D-NAO-281 · 계약 `CONTRACT_pao_purpose_and_hands.md` P2-ⓑ)
//
// ## 왜 이 테스트가 있어야 하나
// 계약이 지목한 합격 표면은 「배포·재시작 없이 바뀌고, **현재값과 「OFF = 카나리 allowlist
// 복귀」 사실이 화면에 보인다**」이다. 백엔드에 SPECS 키를 등재하고 라우터가 내려보내는 것만으로는
// 그 기준을 못 채운다 — 이 저장소가 반복해 데인 결함이 정확히 「만드는 층은 됐는데 닿는 층이
// 비었다」(교훈 #362)이고, 이번 스위치는 **끄는 사람이 OFF의 의미를 오해하면 안 꺼진 캠페인을
// 꺼진 줄 아는** 종류라 오해의 대가가 크다.
//
// 그래서 이 파일이 재는 것은 「필드가 응답에 있다」가 아니라 **사람이 읽는 텍스트에 그 사실이
// 있는가**이고, 특히 그 경고가 **접히지 않은 자리**에 있는가다(접힌 곳에 적은 사실은 없는
// 사실과 같다 — 「근거 보기」 details 안은 그 자리가 아니다).
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

const RESPONSE_BASE = {
  from_db_enabled: true,
  from_db_help: "되돌림 절차 설명",
  retro_freshness: { latest_asof: "2026-08-31", expected_asof: "2026-08-31", stale: false, lag_days: 0 },
  window_coverage: [],
};

// 백엔드 `describe()`가 실제로 내려보내는 모양 그대로.
const ROUTING_ON = {
  key: "ad_bid_routing_enabled",
  label: "소재(ad) 입찰 라우팅",
  value: 1,
  source: "code" as const,
  code_default: 1,
  min: 0,
  max: 1,
  why: "True(D-NAO-125). 소재-레벨 제안 생성과 자동 실행을 함께 여는 스위치다.",
  direction: "tighten_down" as const,
  kind: "bool" as const,
  warn: "OFF = 전면 정지가 아니라 **카나리 allowlist로 복귀**입니다",
  env: null,
  rejected: false,
  env_rejected: false,
  updated_at: null,
};

const CS_DRY_RUN_FROM_ENV = {
  key: "naver_cs_dry_run",
  label: "콜드스타트 레인 dry-run",
  value: 0,
  source: "env" as const,
  code_default: 1,
  min: 0,
  max: 1,
  why: "코드 기본값 True(관측만).",
  direction: "tighten_up" as const,
  kind: "bool" as const,
  warn: "OFF(dry-run 해제)면 콜드스타트 레인이 네이버에 실제로 입찰을 씁니다.",
  env: "NAVER_CS_DRY_RUN",
  rejected: false,
  env_rejected: false,
  updated_at: null,
};

afterEach(() => {
  cleanup();
  h.guardrail = null;
  vi.clearAllMocks();
});

describe("킬스위치 런타임화 — 화면 표면(D-NAO-281 P2-ⓑ)", () => {
  it("★현재값이 1/0이 아니라 «켜짐/꺼짐»으로 읽힌다", async () => {
    h.guardrail = { ...RESPONSE_BASE, params: [ROUTING_ON, CS_DRY_RUN_FROM_ENV] };
    renderPage();
    expect(await screen.findByText(/소재\(ad\) 입찰 라우팅/)).toBeTruthy();
    // ★<option>에도 같은 문구가 있으므로 «현재값 칸»(td)만 골라 센다 — 안 그러면 토글의 선택지
    //   때문에 이 단언이 저절로 참이 되어, 값 칸이 통째로 사라져도 초록이 된다.
    const cell = (t: string) => screen.getAllByText(t).filter((el) => el.tagName === "TD");
    expect(cell("켜짐 (ON)")).toHaveLength(1);   // 라우팅 = ON
    expect(cell("꺼짐 (OFF)")).toHaveLength(1);  // CS dry-run = OFF(env가 0)
  });

  it("★★「OFF = 카나리 allowlist 복귀」가 접히지 않은 자리에 보인다 — 계약이 지목한 표면", async () => {
    h.guardrail = { ...RESPONSE_BASE, params: [ROUTING_ON] };
    renderPage();
    const warn = await screen.findByText(/카나리 allowlist로 복귀/);
    expect(warn).toBeTruthy();
    // ★접힘 검사: 이 문구의 조상 중에 <details>가 있으면 「근거 보기」를 펴야만 보인다는 뜻이고,
    //   그건 이 경고가 있어야 할 자리가 아니다(경고를 details로 옮기는 회귀를 여기서 잡는다).
    expect(warn.closest("details")).toBeNull();
  });

  it("★값을 바꾸는 손이 토글이다(숫자칸 아님) — 스위치를 숫자로 두면 사람이 매번 되묻는다", async () => {
    h.guardrail = { ...RESPONSE_BASE, params: [ROUTING_ON] };
    renderPage();
    const control = await screen.findByLabelText("소재(ad) 입찰 라우팅");
    expect(control.tagName).toBe("SELECT");
    expect((control as HTMLSelectElement).value).toBe("1"); // 현재값이 손에도 반영돼 있다
  });

  it("★env에서 온 값은 «서버 환경변수»라고 말하고, 저장하면 덮어쓴다는 사실도 말한다", async () => {
    // 이걸 안 말하면 사람은 화면 숫자를 자기가 정한 값으로 오해한다.
    // prod `.env`에 NAVER_CS_DRY_RUN=0이 실재하므로 이 행은 라이브에서 실제로 나타난다.
    h.guardrail = { ...RESPONSE_BASE, params: [CS_DRY_RUN_FROM_ENV] };
    renderPage();
    expect(await screen.findByText("서버 환경변수")).toBeTruthy();
    expect(screen.getByText(/NAVER_CS_DRY_RUN 값입니다 — 여기서 저장하면 그 값을 덮어씁니다/)).toBeTruthy();
  });

  it("env 값도 읽을 수 없으면 그 사실을 따로 말한다 — 조용한 폴백 금지", async () => {
    h.guardrail = {
      ...RESPONSE_BASE,
      params: [{ ...CS_DRY_RUN_FROM_ENV, source: "code" as const, value: 1,
                 rejected: true, env_rejected: true }],
    };
    renderPage();
    expect(await screen.findByText(/설정한 값이 허용 범위를 벗어나/)).toBeTruthy();
    expect(screen.getByText(/서버 NAVER_CS_DRY_RUN 값도 읽을 수 없어/)).toBeTruthy();
  });

  it("bool이 아닌 봉투 파라미터는 종전대로 숫자칸이다 — 토글이 봉투까지 삼키지 않는다", async () => {
    h.guardrail = {
      ...RESPONSE_BASE,
      params: [{
        key: "cooldown_hours", label: "같은 유닛 쿨다운", value: 2, source: "code" as const,
        code_default: 2, min: 1, max: 24, why: "2시간(D-NAO-19).",
        direction: "tighten_up" as const, kind: "int" as const,
        warn: null, env: null, rejected: false, env_rejected: false, updated_at: null,
      }],
    };
    renderPage();
    expect(await screen.findByText(/같은 유닛 쿨다운/)).toBeTruthy();
    expect(screen.getByText("2시간")).toBeTruthy();
    expect(screen.queryByText("켜짐 (ON)")).toBeNull();
  });
});
