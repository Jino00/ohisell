// @vitest-environment jsdom
//
// naverAdScopeBulkAndVoid.test.tsx — H5 일괄 지정 + 원장 무효화(void)가 «사람에게 닿는가»
// (계약 `CONTRACT_pao_purpose_and_hands.md` §6 P2 「void 버튼·스코프 일괄 지정」)
//
// ## 왜 이 파일이 따로 있나
// 백엔드 회귀(`test_naver_scope_bulk_h5.py`)는 「엔드포인트가 옳게 도나」를 지킨다. 그건
// 「사람이 그 손을 쓸 수 있나」를 못 묻는다 — 이 저장소가 반복해 밟은 병이고, void가 정확히
// 그 모양으로 누워 있었다: **백엔드는 완비인데 프론트 호출부가 0건**이라 부를 손이 없었다.
// 그러니 여기서 재는 것은 값이 아니라 **마운트·전달·문구**다.
//
// ★캠페인 카드는 `has_scope=true`면 **기본으로 펼쳐진다**(`useState(c.has_scope)`)— 그래서
//   펼치는 클릭이 필요 없다. 그리고 캠페인 «이름»으로 찾으면 안 된다: 같은 이름이 「정지된
//   캠페인」 요약줄에도 나와 `findByText`가 중복 매치로 던진다(내 초판이 그 실수였다).
//
// 죽여야 할 표면 변이:
//   BV-1 `<BulkScopeBar/>` 마운트 한 줄 제거 → 일괄 손이 화면에서 사라진다
//   BV-2 일괄이 «보이는 목록» 대신 빈 배열/서버 위임으로 보냄 → 본 것과 손댄 것이 어긋난다
//   BV-3 결과 문구가 `changed` 대신 `requested`를 말함 → no-op까지 「했다」로 읽힌다
//   BV-4 `<VoidButton/>` 마운트 제거 → 백엔드만 있고 손이 없던 원상태로 되돌아간다
//   BV-5 ★`wisdom_may_have_counted: null`을 「아니오」로 접음 → «확인 안 함»이 «안 셌음»으로
//        둔갑한다(교훈 #123). 3상을 3상으로 말하는지 문구로 잰다.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { PaoScopeRoster } from "../lib/api";
// ★쓰기 표면을 재려면 함수 «자신»을 vi.fn()으로 잡아야 한다(같은 폴더 SUR-5의 교훈).
import { putPaoScopeCampaignBulk, voidNaverSearchTermExecution } from "../lib/api";

// ★`vi.mock` 팩토리는 **import 시점**에 평가된다 — 모듈 상수(ROSTER 등)를 직접 참조하면
//   초기화 전 접근(TDZ)이라 팩토리가 통째로 터지고, 증상은 「화면이 안 뜬다」로만 보인다.
//   그래서 팩토리가 읽는 것은 전부 `hoisted`를 거친다(기존 paoScopeReachesTheUser와 같은 관례).
const hoisted = vi.hoisted(() => ({
  roster: null as unknown,
  bulkResult: null as unknown,
  voidResult: null as unknown,
  exclusionRows: [] as unknown[],
}));

const ADGROUPS = [
  {
    adgroup_id: "grp-s25fe", name: "S25FE", status: "on",
    in_scope: true, scope_role: "accel", scope_enabled: true,
    cost: 10_000, imp: 100, clk: 10, conv_amt: 30_000, roas: 3.0, bep_roas: 1.711,
    baseline_days: 14, gross_profit: 7_534, gross_profit_low: 1_200, gross_profit_high: 15_000,
    profit_status: "ok",
  },
  {
    adgroup_id: "grp-z8wide", name: "Z폴드8와이드", status: "on",
    in_scope: false, scope_role: null, scope_enabled: null,
    cost: 90_000, imp: 900, clk: 40, conv_amt: 90_000, roas: 1.0, bep_roas: null,
    baseline_days: 14, gross_profit: null, gross_profit_low: null, gross_profit_high: null,
    profit_status: "bep_unknown",
  },
];

const ROSTER: PaoScopeRoster = {
  window: { date_from: "2026-08-11", date_to: "2026-08-31", days: 21 },
  correction_factor: { low: 0.827, high: 1.336, source: "actual_revenue_ratio" },
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
    basis: "ad_date (성과 발생일)",
    reference: "ref 63 §4-1",
  },
  campaigns: [{
    campaign_id: "cmp-tpu", name: "01. 갤럭시_지문방지_TPU", campaign_type: "SHOPPING",
    optimizer: "ours", auto_operate: false, has_scope: true, scoped_count: 1,
    adgroup_count: 2, ramp_up_count: 0,
    cost: 100_000, imp: 1000, clk: 50, conv_amt: 120_000, roas: 1.2,
    gross_profit: -30_000, gross_profit_low: -60_000, gross_profit_high: 10_000,
    adgroups: ADGROUPS,
  }],
} as unknown as PaoScopeRoster;

const EXCLUSION_ROW = {
  id: 77, campaign_id: "cmp-tpu", campaign_name: "01. 갤럭시_지문방지_TPU",
  adgroup_id: "grp-s25fe", search_term: "아이패드종이필름", status: "excluded",
  cycle: 1, source: "ss_lane", next_review_at: "2026-09-10",
  probation_until: null, reopen_block_reason: null,
};

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchPaoScopeRoster: vi.fn(async () => hoisted.roster),
    putPaoScopeAdgroup: vi.fn(async () => ({
      campaign_id: "cmp-tpu", adgroup_id: "grp-s25fe", role: "accel" as const,
      enabled: true, memo: null,
    })),
    deletePaoScopeAdgroup: vi.fn(async () => ({
      deleted: true, remaining_rows: 0, campaign_now_unrestricted: true,
    })),
    putNaverCampaignAutoOperate: vi.fn(async () => ({
      campaign_id: "cmp-tpu", optimizer: "ours" as const, auto_operate: true,
      mode: null, target_roas_override: null, memo: null, loss_policy: null,
      updated_at: "2026-09-01T16:00:00",
    })),
    fetchNaverCampaignIgnitionPreflight: vi.fn(async () => ({
      campaign_id: "cmp-tpu", auto_operate: false, optimizer: "ours" as const,
      safe_to_ignite: true, warnings: [],
    })),
    fetchNaverSearchTermExclusions: vi.fn(async () => ({
      total: hoisted.exclusionRows.length, summary_by_status: {},
      today_excluded: 0, today_opened: 0, today_restored: 0,
      rows: hoisted.exclusionRows,
    })),
    reopenNaverSearchTermExclusion: vi.fn(async () => ({
      ok: true, id: 77, status: "probation", reason: null, probation_until: "2026-09-14",
    })),
    putPaoScopeCampaignBulk: vi.fn(async () => hoisted.bulkResult),
    voidNaverSearchTermExecution: vi.fn(async () => hoisted.voidResult),
    fetchHealth: vi.fn(async () => { throw new Error("not needed"); }),
    fetchSchedulerStatus: vi.fn(async () => { throw new Error("not needed"); }),
  };
});

beforeEach(() => {
  hoisted.bulkResult = {
    campaign_id: "cmp-tpu", requested: 2, changed: 1,
    counts: { created: 1, updated: 0, unchanged: 1 },
    rows: [
      { adgroup_id: "grp-z8wide", outcome: "created", role: "accel", enabled: true },
      { adgroup_id: "grp-s25fe", outcome: "unchanged", role: "accel", enabled: true },
    ],
  };
  hoisted.voidResult = {
    result: "voided", exclusion_id: 77, status: "void", previous_status: "excluded",
    diary_voided: 1, wisdom_may_have_counted: null, diary_note: null,
  };
  hoisted.roster = ROSTER;
  hoisted.exclusionRows = [EXCLUSION_ROW];
  window.history.pushState({}, "", "/naver-ad/scope");
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

async function renderApp() {
  const { default: App } = await import("../App");
  return render(<App />);
}

// ── H5 일괄 지정 ────────────────────────────────────────────────────────
describe("H5 — 스코프 캠페인 단위 일괄 지정이 사람에게 닿는다", () => {
  it("BV-1: 일괄 손이 스코프 화면에 «마운트»돼 있다", async () => {
    await renderApp();
    expect(await screen.findByRole("button", { name: "전부 맡김" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "전부 끄기" })).toBeTruthy();
  });

  it("BV-1b: 「엔진을 켜지는 않습니다」를 화면이 말한다", async () => {
    await renderApp();
    expect(await screen.findByText(/엔진을 켜지는 않습니다/)).toBeTruthy();
  });

  it("BV-2: ★«보이는» 광고그룹 id를 그대로 보낸다 — 「전부」를 서버에 맡기지 않는다", async () => {
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "전부 맡김" }));

    await waitFor(() => expect(putPaoScopeCampaignBulk).toHaveBeenCalled());
    const arg = vi.mocked(putPaoScopeCampaignBulk).mock.calls[0][0];
    expect(arg.campaign_id).toBe("cmp-tpu");
    expect(arg.adgroup_ids).toEqual(["grp-s25fe", "grp-z8wide"]);
    expect(arg.enabled).toBe(true);
  });

  it("BV-2b: 「전부 끄기」는 enabled=false로 보낸다(행 삭제가 아니다)", async () => {
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "전부 끄기" }));

    await waitFor(() => expect(putPaoScopeCampaignBulk).toHaveBeenCalled());
    expect(vi.mocked(putPaoScopeCampaignBulk).mock.calls[0][0].enabled).toBe(false);
  });

  it("BV-3: ★결과 문구가 `changed`를 말한다 — `requested`(2건)가 아니라 1건", async () => {
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "전부 맡김" }));

    // requested=2, changed=1, unchanged=1인 응답이다. 화면이 「2건」이라 말하면 no-op까지
    // 「했다」로 세는 것이라 감사 원장의 줄 수(1)와 어긋난다.
    expect(await screen.findByText(/1건 바뀜/)).toBeTruthy();
    expect(await screen.findByText(/1건은 이미 같은 값/)).toBeTruthy();
  });

  it("확인 대화상자를 거절하면 서버를 부르지 않는다", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "전부 맡김" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "전부 맡김" })).toBeTruthy());
    expect(putPaoScopeCampaignBulk).not.toHaveBeenCalled();
  });
});

// ── 원장 무효화(void) ───────────────────────────────────────────────────
describe("void — 원장 무효화 손이 사람에게 닿는다", () => {
  it("BV-4: 무효화 버튼이 제외 행 옆에 «마운트»돼 있다", async () => {
    await renderApp();
    expect(await screen.findByRole("button", { name: "무효화" })).toBeTruthy();
  });

  it("사유를 받아 그대로 서버에 보낸다(사유는 필수다)", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("오타로 잘못 등록함");
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "무효화" }));

    await waitFor(() => expect(voidNaverSearchTermExecution).toHaveBeenCalled());
    expect(vi.mocked(voidNaverSearchTermExecution).mock.calls[0]).toEqual([77, "오타로 잘못 등록함"]);
  });

  it("빈 사유는 서버로 보내지 않는다", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("   ");
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "무효화" }));

    expect(await screen.findByText(/사유가 비어 무효화하지 않았습니다/)).toBeTruthy();
    expect(voidNaverSearchTermExecution).not.toHaveBeenCalled();
  });

  it("취소(prompt=null)하면 아무 일도 일어나지 않는다", async () => {
    vi.spyOn(window, "prompt").mockReturnValue(null);
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "무효화" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "무효화" })).toBeTruthy());
    expect(voidNaverSearchTermExecution).not.toHaveBeenCalled();
  });

  it("BV-5: ★`wisdom_may_have_counted: null`을 «아니오»로 접지 않는다", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("사유");
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "무효화" }));

    // null은 「확인하지 못했습니다」여야 한다. 「아직 안 들어갔습니다」(=false의 문구)로
    // 나오면 «확인 안 함»이 «안 셌음»으로 둔갑한 것이다.
    expect(await screen.findByText(/확인하지 못했습니다/)).toBeTruthy();
    expect(screen.queryByText(/아직 안 들어갔습니다/)).toBeNull();
  });

  it("BV-5b: `true`와 `false`는 서로 다른 문구로 말한다", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("사유");
    hoisted.voidResult = {
      result: "voided", exclusion_id: 77, status: "void", previous_status: "excluded",
      diary_voided: 2, wisdom_may_have_counted: true, diary_note: null,
    };
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "무효화" }));

    expect(await screen.findByText(/이미 학습에 셈이 들어갔을 수 있습니다/)).toBeTruthy();
    expect(screen.queryByText(/확인하지 못했습니다/)).toBeNull();
  });

  it("멱등 — 이미 무효화된 행이면 그 사실을 말한다", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("사유");
    hoisted.voidResult = {
      result: "already_void", exclusion_id: 77, status: "void",
      diary_voided: 0, wisdom_may_have_counted: null,
      diary_note: "이미 무효화된 행이다",
    };
    await renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "무효화" }));

    expect(await screen.findByText(/이미 무효화된 행입니다/)).toBeTruthy();
  });
});
