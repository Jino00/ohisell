// @vitest-environment jsdom
//
// naverAdReopenHandReachesTheUser.test.tsx — 재개방의 «손»이 사람에게 닿는가 (계약 P2 넷째)
//
// ## 왜 이 파일이 따로 있나
//
// 재개방의 **유형별 dispatch는 이미 있었다**(D-NAO-271). 없던 것은 손이다 — 그 dispatch가
// 자동 레인 «안»에서만 돌았고, 레인은 `auto_operate=1`인 캠페인만 훑는다. 그래서 스위치가 꺼진
// 캠페인의 제외는 재심사일이 지나도 아무도 못 열었다(2026-08-31 실측: due 1건이 10일째 대기).
//
// 그러니 이 파일이 재는 것은 **「함수가 값을 만드나」가 아니라 「사람이 그걸 누를 수 있나」**다.
// 죽여야 할 표면 변이 넷:
//
//   RSUR-1 재개방 패널 자체를 안 그림 — 제외가 있어도 화면에 아무것도 없다
//   RSUR-2 버튼 클릭이 **서버에 안 닿음**(onClick no-op) — 눌러도 아무 일도 안 일어난다
//   RSUR-3 **비활성 사유를 안 그림** — 회색 버튼만 남아 「왜 못 여는지」가 사라진다
//   RSUR-4 막힌 응답(`ok:false`)의 **사유를 안 보여줌** — 눌렀는데 조용히 아무 일도 없다
//
// ★RSUR-3·4가 이 파일의 핵심이다. 버튼이 «있는데 눌리지 않는» 상태는 사유가 없으면
//   「고장」과 구별되지 않는다 — 이 트랙에서 가장 자주 재발한 모양이 그것이다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { PaoScopeRoster } from "../lib/api";
import { reopenNaverSearchTermExclusion } from "../lib/api";

const hoisted = vi.hoisted(() => ({ rows: [] as unknown[], reopenResult: null as unknown }));

const OPENABLE = {
  id: 1, campaign_id: "cmp-tpu", adgroup_id: "grp-web", search_term: "아이패드종이필름",
  status: "excluded", cycle: 1, source: null,
  next_review_at: "2026-08-21", probation_until: null,
  reopen_block_reason: null, // ★지금 열 수 있다
};

const BLOCKED = {
  ...OPENABLE, id: 2, search_term: "골프",
  reopen_block_reason: "자동운영 OFF — 켠 뒤 재개방",
};

const CONSOLE_IMPORT = { ...OPENABLE, id: 3, search_term: "대행사가건것", source: "console_import" };

const ROSTER: PaoScopeRoster = {
  window: { date_from: "2026-08-10", date_to: "2026-08-31", days: 21 },
  correction_factor: { low: 0.827, high: 1.3016, source: "actual_revenue_ratio" },
  totals: { cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000 },
  weekend_holiday: {
    weekday: { days: 15, cost: 80_000, imp: 800, clk: 40, conv_amt: 110_000, roas: 1.375 },
    weekend: { days: 5, cost: 18_000, imp: 180, clk: 9, conv_amt: 9_000, roas: 0.5 },
    holiday: { days: 1, cost: 2_000, imp: 20, clk: 1, conv_amt: 1_000, roas: 0.5 },
    identity: {
      total: { cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000 },
      sum_of_parts: { cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000 },
      ok: true, note: "평시+주말+공휴일 = 전체",
    },
    basis: "ad_date", reference: "ref 63 §4-1",
  },
  campaigns: [{
    campaign_id: "cmp-tpu", name: "01. 갤럭시_지문방지_TPU", campaign_type: "SHOPPING",
    optimizer: "ours", auto_operate: false, has_scope: false,
    scoped_count: 0, adgroup_count: 1, ramp_up_count: 0,
    cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000, roas: 1.2,
    gross_profit: -30_000, gross_profit_low: -60_000, gross_profit_high: 10_000,
    adgroups: [{
      adgroup_id: "grp-web", name: "웹사이트", status: "on",
      in_scope: false, scope_role: null, scope_enabled: null,
      cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000, roas: 1.2,
      bep_roas: 1.711, baseline_days: 14,
      gross_profit: -30_000, gross_profit_low: -60_000, gross_profit_high: 10_000,
      profit_status: "ok",
    }],
  }],
};

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchPaoScopeRoster: vi.fn(async () => ROSTER),
    fetchNaverCampaignIgnitionPreflight: vi.fn(async () => ({
      campaign_id: "cmp-tpu", auto_operate: false, optimizer: "ours",
      safe_to_ignite: true, warnings: [],
    })),
    putNaverCampaignAutoOperate: vi.fn(async () => ({
      campaign_id: "cmp-tpu", optimizer: "ours", auto_operate: true,
      mode: null, target_roas_override: null, memo: null, loss_policy: null,
      updated_at: "2026-08-31T17:00:00",
    })),
    fetchNaverSearchTermExclusions: vi.fn(async () => ({
      total: hoisted.rows.length, summary_by_status: { excluded: hoisted.rows.length },
      today_excluded: 0, today_opened: 0, today_restored: 0, rows: hoisted.rows,
    })),
    // ★함수 «자신»을 vi.fn()으로 잡아야 「클릭이 서버에 닿나」를 잴 수 있다(SUR-5의 교훈).
    reopenNaverSearchTermExclusion: vi.fn(async () => hoisted.reopenResult),
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  hoisted.rows = [OPENABLE];
  hoisted.reopenResult = { ok: true, id: 1, status: "probation", reason: null, probation_until: "2026-09-14" };
  window.history.pushState({}, "", "/naver-ad/scope");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderApp() {
  const { default: App } = await import("../App");
  return render(<App />);
}

/** 캠페인 블록을 펴야 패널이 보인다(접혀 있으면 렌더 자체가 없다). */
async function openCampaign() {
  await renderApp();
  const toggle = await screen.findByText("01. 갤럭시_지문방지_TPU");
  fireEvent.click(toggle);
}

describe("★재개방의 손이 사람에게 닿는 경로", () => {
  it("RSUR-1: 우리가 건 제외가 있으면 재개방 패널과 검색어가 화면에 뜬다", async () => {
    await openCampaign();
    expect(await screen.findByText("검색어 제외 재개방")).toBeTruthy();
    expect(await screen.findByText("아이패드종이필름")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "지금 재개방" })).toBeTruthy();
  });

  it("RSUR-2: 「지금 재개방」 클릭이 실제로 서버 경로를 부른다(그 행 id로)", async () => {
    await openCampaign();
    fireEvent.click(await screen.findByRole("button", { name: "지금 재개방" }));
    await waitFor(() => {
      expect(vi.mocked(reopenNaverSearchTermExclusion)).toHaveBeenCalledWith(1);
    });
  });

  it("RSUR-3: 못 여는 건은 버튼이 비활성이고 «사유»가 옆에 적힌다", async () => {
    hoisted.rows = [BLOCKED];
    await openCampaign();
    const btn = await screen.findByRole("button", { name: "지금 재개방" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    // ★사유가 없으면 회색 버튼은 「고장」과 구별되지 않는다.
    expect(await screen.findByText(/자동운영 OFF — 켠 뒤 재개방/)).toBeTruthy();
  });

  it("RSUR-4: 서버가 막으면(ok:false) 그 사유가 화면에 뜬다 — 조용한 no-op 금지", async () => {
    hoisted.reopenResult = {
      ok: false, id: 1, status: "excluded",
      reason: "오늘 복귀 캡 소진 — 내일 레인에서 다시 열린다", reason_code: "daily_cap",
    };
    await openCampaign();
    fireEvent.click(await screen.findByRole("button", { name: "지금 재개방" }));
    expect(await screen.findByText(/오늘 복귀 캡 소진/)).toBeTruthy();
  });

  it("성공하면 무엇이 열렸고 언제까지 관찰하는지 말한다", async () => {
    await openCampaign();
    fireEvent.click(await screen.findByRole("button", { name: "지금 재개방" }));
    expect(await screen.findByText(/열었습니다.*아이패드종이필름.*2026-09-14/)).toBeTruthy();
  });

  it("콘솔 편입분(console_import)은 목록에 아예 안 나온다 — 우리가 건 제외가 아니다", async () => {
    hoisted.rows = [CONSOLE_IMPORT];
    await openCampaign();
    expect(await screen.findByText(/우리가 건 검색어 제외가 없습니다/)).toBeTruthy();
    expect(screen.queryByText("대행사가건것")).toBeNull();
  });
});
