// @vitest-environment jsdom
//
// paoScopeReachesTheUser.test.tsx — 「🎛️ PAO 스코프」가 **사람에게 실제로 닿는가** (D-NAO-244)
//
// ## 왜 이 파일이 따로 있나
//
// 계약 §4의 마지막 항목이 **표면 절단 변이**를 요구한다. 이 저장소가 다섯 번 밟은 병이
// 「값은 만드는데 사람이 못 본다」이고(전역 §4 ★), `costPageReachesTheUser.test.tsx`가
// 그 병을 잡으려고 세운 관례가 이 파일의 본이다. 죽여야 할 변이 넷:
//
//   SUR-1 `App.tsx`의 `/naver-ad/scope` **라우트** 제거
//   SUR-2 `Layout.tsx`의 좌측 「🎛️ PAO 스코프」 **메뉴** 제거
//   SUR-3 `pao_scope_roster`가 실어 보낸 **스코프 상태**(맡김·역할)를 화면이 안 그림
//   SUR-4 총이익 **null을 0원으로** 그림 — 적자 그룹이 손익분기로 보인다
//
// **`App`을 통째로 `/naver-ad/scope`에서 렌더한다.** 라우팅·레이아웃·페이지·직렬화가 한 줄로
// 이어져야만 통과하므로 어느 하나만 끊어도 죽는다. api 모듈은 모킹해 네트워크를 안 탄다 —
// 재는 것은 「값이 화면 픽셀이 되나」이지 서버가 아니다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import type { PaoScopeRoster } from "../lib/api";

const ROSTER: PaoScopeRoster = {
  window: { date_from: "2026-08-03", date_to: "2026-08-23", days: 21 },
  correction_factor: { value: 1.0, source: "actual_revenue_ratio" },
  totals: {},
  campaigns: [
    {
      campaign_id: "cmp-tpu",
      name: "01. 갤럭시_지문방지_TPU",
      campaign_type: "SHOPPING",
      optimizer: "ours",
      auto_operate: false, // ★스코프는 있는데 엔진은 꺼져 있는 상태 — 화면이 말해야 한다
      has_scope: true,
      scoped_count: 1,
      adgroup_count: 2,
      cost: 100_000,
      imp: 1000,
      clk: 50,
      conv_amt: 120_000,
      roas: 1.2,
      gross_profit: -30_000,
      adgroups: [
        {
          adgroup_id: "grp-s25fe",
          name: "S25FE",
          status: "on",
          in_scope: true,
          scope_role: "accel",
          scope_enabled: true,
          cost: 10_000,
          imp: 100,
          clk: 10,
          conv_amt: 30_000,
          roas: 3.0,
          bep_roas: 1.711,
          gross_profit: 7_534,
          profit_status: "ok",
        },
        {
          adgroup_id: "grp-z8wide",
          name: "Z폴드8와이드",
          status: "on",
          in_scope: false,
          scope_role: null,
          scope_enabled: null,
          cost: 90_000,
          imp: 900,
          clk: 40,
          conv_amt: 90_000,
          roas: 1.0,
          bep_roas: null,
          gross_profit: null, // ★모름 — 0원이 아니다
          profit_status: "bep_unknown",
        },
      ],
    },
  ],
};

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchPaoScopeRoster: vi.fn(async () => ROSTER),
    putPaoScopeAdgroup: vi.fn(async () => ({
      campaign_id: "cmp-tpu", adgroup_id: "grp-s25fe", role: "accel" as const,
      enabled: true, memo: null,
    })),
    deletePaoScopeAdgroup: vi.fn(async () => ({
      deleted: true, remaining_rows: 0, campaign_now_unrestricted: true,
    })),
    // 레이아웃이 부르는 헬스/스케줄러류는 조용히 실패시켜도 이 화면 판정과 무관하다.
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  window.history.pushState({}, "", "/naver-ad/scope");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderApp() {
  // 동적 임포트 — vi.mock이 먼저 걸린 뒤 App이 api를 집게 하려면 이 순서여야 한다.
  const { default: App } = await import("../App");
  return render(<App />);
}

describe("★「🎛️ PAO 스코프」가 사람에게 닿는 경로 — 라우트·메뉴·직렬화가 한 줄로 이어진다", () => {
  it("SUR-1: `/naver-ad/scope` 라우트가 있어야 PAO 스코프 화면이 뜬다", async () => {
    await renderApp();
    expect(await screen.findByRole("heading", { name: /PAO 스코프/ })).toBeTruthy();
  });

  it("SUR-2: 좌측 메뉴에 「PAO 스코프」가 있어야 사람이 찾아올 수 있다", async () => {
    await renderApp();
    const links = await screen.findAllByRole("link", { name: /PAO 스코프/ });
    expect(links.length).toBeGreaterThan(0);
    expect(links.some((a) => a.getAttribute("href") === "/naver-ad/scope")).toBe(true);
  });

  it("SUR-3: 스코프 상태(맡김·역할)가 화면 픽셀이 된다", async () => {
    await renderApp();
    // 맡긴 그룹은 「맡김」으로, 안 맡긴 그룹은 「안 맡김」으로 그려져야 한다
    expect(await screen.findByTitle(/이 그룹을 끕니다/)).toBeTruthy();   // in_scope=true 쪽
    expect(await screen.findByTitle(/이 그룹을 엔진에 맡깁니다/)).toBeTruthy(); // in_scope=false 쪽
    // 역할 select가 accel을 실제로 선택하고 있어야 한다(직렬화가 끊기면 빈 값이 된다)
    const selects = await screen.findAllByRole("combobox");
    expect(selects.some((s) => (s as HTMLSelectElement).value === "accel")).toBe(true);
  });

  it("SUR-4: 총이익 null은 «모름»으로 그린다 — 0원으로 그리면 적자가 손익분기로 보인다", async () => {
    await renderApp();
    await waitFor(() => expect(screen.getAllByText("모름").length).toBeGreaterThan(0));
    // 그리고 그 행에 「0」이 총이익으로 찍히지 않았는지 — 모름과 0을 뭉치면 이 테스트가 죽는다
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it("★엔진이 꺼져 있으면 화면이 그렇게 말한다 — 「맡겼다」와 「돌고 있다」를 뭉치지 않는다", async () => {
    await renderApp();
    expect(
      await screen.findByText(/스코프는 지정돼 있지만 엔진은 이 캠페인에서 돌지 않습니다/),
    ).toBeTruthy();
  });

  it("★스코프가 있는 캠페인은 「예산은 엔진이 안 만진다」를 화면에서 밝힌다", async () => {
    await renderApp();
    expect(await screen.findByText(/캠페인 예산 조정은 엔진이 하지 않습니다/)).toBeTruthy();
  });
});
